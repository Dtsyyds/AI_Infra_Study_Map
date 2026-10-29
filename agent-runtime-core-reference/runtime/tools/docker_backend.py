"""固定资料检查程序的 Docker 隔离边界；没有任意代码/镜像执行入口。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

IMAGE = (
    "python:3.11.17-slim-bookworm@sha256:"
    "f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf"
)
PROJECT_LABEL = "io.agent-runtime-core-reference.project"
ATTEMPT_LABEL = "io.agent-runtime-core-reference.attempt"
TASK_LABEL = "io.agent-runtime-core-reference.task"
FENCE_LABEL = "io.agent-runtime-core-reference.fence"
CONTAINER_PREFIX = "arc-ref-tool-"

# 业务判断只读输入字段和检索到的规则；文档 ID 不参与判断。
CHECK_SCRIPT = r'''
import json, sys
data = json.load(sys.stdin)
doc, rules = data["document"], data["rules"]
missing, citations = [], []
for rule in rules:
    key = rule["rule_id"]
    if rule["rule_type"] == "approval_threshold" and doc["amount"] > rule["threshold"]:
        if "approval" not in doc["attachments"]:
            missing.append("approval")
    elif rule["rule_type"] == "invoice_required" and "invoice" not in doc["attachments"]:
        missing.append("invoice")
    citations.append({"rule_id": key, "version": rule["version"], "source": rule["source"]})
print(json.dumps({"document_id": doc["document_id"], "document_version": doc["version"],
                  "review_decision": "NEEDS_DOCUMENTS" if missing else "PASS",
                  "missing_documents": missing, "citations": citations}))
'''

# 只能由受信测试代码选择，普通 API / LLM 参数不能传入脚本。
TIMEOUT_SCRIPT = r'''
import os, signal, time
print("ARC_REF_STARTED", flush=True)
while True:
    signal.pause()
'''


class DockerUnavailable(RuntimeError):
    """基础设施阻塞；调用方必须记录 BLOCKED，不能使用宿主执行代替。"""


class DockerExecutionError(RuntimeError):
    pass


class DockerBackend:
    def __init__(self, *, test_mode: bool = False, image: str = IMAGE) -> None:
        if image != IMAGE:
            raise ValueError("only the pinned teaching image is allowed")
        self.image = image
        self.test_mode = test_mode
        self._active: set[str] = set()
        self.evidence: list[dict[str, Any]] = []

    async def _docker(self, *args: str, max_seconds: float = 15) -> tuple[int, str, str]:
        # 空 CLI 配置防止 docker 自动读取使用者既有 registry 凭据。
        with tempfile.TemporaryDirectory(prefix="arc-ref-docker-") as config_dir:
            process = await asyncio.create_subprocess_exec(
                "docker", "--config", config_dir, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.defpath, "DOCKER_HOST": "unix:///var/run/docker.sock"},
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), max_seconds)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            return process.returncode or 0, stdout.decode(), stderr.decode()

    async def availability(self) -> dict[str, Any]:
        try:
            code, stdout, stderr = await self._docker("version", "--format", "{{json .}}")
        except (OSError, TimeoutError) as exc:
            return {"available": False, "reason": type(exc).__name__, "image": self.image}
        if code:
            reason = "Docker socket permission denied" if "permission denied" in stderr else "Docker daemon unavailable"
            return {"available": False, "reason": reason, "image": self.image}
        try:
            version = json.loads(stdout)
            if not isinstance(version, dict) or not version.get("Server"):
                raise ValueError("missing server")
        except (ValueError, TypeError):
            return {"available": False, "reason": "invalid daemon version response", "image": self.image}
        return {"available": True, "version": version, "image": self.image}

    async def inspect_state(self, identifier: str) -> dict[str, Any]:
        """ABSENT requires a specific daemon response AND a reachable daemon."""
        result: dict[str, Any] = {"identifier": identifier, "status": "UNKNOWN"}
        try:
            code, stdout, stderr = await self._docker("container", "inspect", identifier)
            result.update(exit_code=code, stderr=stderr)
            if code:
                expected = f"Error response from daemon: No such container: {identifier}"
                if stderr.strip() == expected and (await self.availability())["available"]:
                    result["status"] = "ABSENT"
            else:
                records = json.loads(stdout)
                if not isinstance(records, list) or len(records) != 1:
                    raise ValueError("invalid inspect records")
                record = records[0]
                if not isinstance(record["State"]["Running"], bool):
                    raise ValueError("invalid container state")
                if not record["Id"] or not record["Name"] or not isinstance(record["Config"], dict):
                    raise ValueError("invalid container identity")
                result.update(status="RUNNING" if record["State"]["Running"] else "STOPPED",
                              record=record)
        except (OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            result["error_type"] = type(exc).__name__
        self.evidence.append(result)
        return result

    async def _inspect_owned(self, identifier: str) -> dict[str, Any] | None:
        inspection = await self.inspect_state(identifier)
        if inspection["status"] == "ABSENT":
            return None
        if inspection["status"] == "UNKNOWN":
            raise DockerUnavailable("container state UNKNOWN; cleanup unconfirmed")
        record: dict[str, Any] = inspection["record"]
        labels = record["Config"].get("Labels") or {}
        name = str(record["Name"]).lstrip("/")
        if labels.get(PROJECT_LABEL) != "reference" or not name.startswith(CONTAINER_PREFIX):
            raise DockerExecutionError("refusing cleanup: ownership mismatch")
        return record

    async def _remove_owned(self, identifier: str) -> None:
        record = await self._inspect_owned(identifier)
        if record is None:
            return
        if record["State"]["Running"]:
            code, _, _ = await self._docker("kill", record["Id"])
            # A race with natural exit is safe only after a successful stopped inspection.
            self.evidence.append({"operation": "kill", "id": record["Id"], "exit_code": code})
            stopped = await self._inspect_owned(record["Id"])
            if stopped is None or stopped["State"]["Running"]:
                raise DockerExecutionError("container termination unconfirmed")
        code, _, _ = await self._docker("rm", record["Id"])
        self.evidence.append({"operation": "remove", "id": record["Id"], "exit_code": code})
        if code or (await self.inspect_state(record["Id"]))["status"] != "ABSENT":
            raise DockerExecutionError("owned container cleanup unconfirmed")

    async def cancel_all(self) -> None:
        for name in tuple(self._active):
            await self._remove_owned(name)
            self._active.discard(name)

    async def cleanup_orphans(
        self, is_attempt_active: Callable[[str, str, int], Awaitable[bool | None]],
    ) -> list[str]:
        """DB 逐容器确认 Attempt 已失效后才清理；DB 不可用时直接失败关闭。"""
        code, stdout, _ = await self._docker(
            "ps", "--all", "--quiet", "--filter", f"label={PROJECT_LABEL}=reference",
            "--filter", f"name={CONTAINER_PREFIX}",
        )
        if code:
            raise DockerUnavailable("cannot inspect reference containers")
        removed = []
        for identifier in stdout.splitlines():
            record = await self._inspect_owned(identifier)
            if record is None:
                continue
            labels = record["Config"]["Labels"]
            task_id = labels.get(TASK_LABEL, "")
            attempt_id = labels.get(ATTEMPT_LABEL, "")
            fence = labels.get(FENCE_LABEL, "")
            try:
                UUID(task_id)
                UUID(attempt_id)
                numeric_fence = int(fence)
            except (ValueError, TypeError):
                # 名称/标签不完整时也不猜测所有权。
                continue
            active = await is_attempt_active(task_id, attempt_id, numeric_fence)
            self.evidence.append({"operation": "ownership_check", "id": record["Id"],
                                  "task_id": task_id, "attempt_id": attempt_id,
                                  "fence": numeric_fence, "active": active})
            if active is False:
                await self._remove_owned(record["Id"])
                removed.append(record["Id"])
        return removed

    def command(self, name: str, attempt_id: str, fence: int, script: str, task_id: str = "") -> list[str]:
        return [
            "run", "--interactive", "--name", name,
            "--label", f"{TASK_LABEL}={task_id}",
            "--label", f"{PROJECT_LABEL}=reference",
            "--label", f"{ATTEMPT_LABEL}={attempt_id}",
            "--label", f"{FENCE_LABEL}={fence}",
            "--network", "none", "--read-only", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--cpus", "0.5", "--memory", "128m", "--memory-swap", "128m",
            "--pids-limit", "32", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--workdir", "/tmp", self.image, "python", "-I", "-B", "-c", script,
        ]

    async def run_fixed(self, script: str, payload: bytes, *, task_id: str,
                        attempt_id: str, fence: int, run_id: str,
                        wall_seconds: float, output_bytes: int,
                        on_test_started: Callable[[str], Awaitable[None]] | None = None) -> dict[str, Any]:
        """Internal trusted runner API, never exposed as a tool argument schema."""
        for identity in (task_id, attempt_id, run_id):
            UUID(identity)
        if not 0 < wall_seconds <= 30 or len(payload) > 2*1024*1024 or not 1024 <= output_bytes <= 1048576:
            raise ValueError("invalid runner resource budget")
        if not (await self.availability())["available"]:
            raise DockerUnavailable("Docker unavailable; no host fallback")
        name = f"{CONTAINER_PREFIX}{task_id}-{fence}-{run_id}"
        result: dict[str, Any] = {"container_name": name, "exit_code": None,
                                  "timed_out": False, "resource_error": False,
                                  "cleanup_confirmed": False}
        stdout, stderr = bytearray(), bytearray()
        notified = False
        async def read(stream: asyncio.StreamReader, target: bytearray) -> None:
            nonlocal notified
            while block := await stream.read(4096):
                target.extend(block)
                if len(stdout)+len(stderr) > output_bytes:
                    raise ValueError("OUTPUT_LIMIT")
                if target is stdout and not notified and b"ARC_REPAIR_TEST_ENTERED\n" in stdout:
                    notified = True
                    if on_test_started:
                        await on_test_started(name)
        offset = len(self.evidence)
        with tempfile.TemporaryDirectory(prefix="arc-ref-docker-") as config_dir:
            process = await asyncio.create_subprocess_exec(
                "docker", "--config", config_dir,
                *self.command(name, attempt_id, fence, script, task_id),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.defpath, "DOCKER_HOST": "unix:///var/run/docker.sock"})
            self._active.add(name)
            readers: list[asyncio.Task[Any]] = []
            try:
                assert process.stdin and process.stdout and process.stderr
                readers = [asyncio.create_task(read(process.stdout, stdout)),
                           asyncio.create_task(read(process.stderr, stderr))]
                async with asyncio.timeout(wall_seconds):
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    process.stdin.close()
                    await asyncio.gather(*readers, process.wait())
                result["exit_code"] = process.returncode
                record = await self._inspect_owned(name)
                if record is not None:
                    result["container"] = record
                    result["resource_error"] = bool(record["State"].get("OOMKilled"))
            except TimeoutError:
                result["timed_out"] = True
            except asyncio.CancelledError:
                result["cancelled"] = True
                raise
            except (ValueError, BrokenPipeError, ConnectionResetError):
                result["resource_error"] = True
            finally:
                try:
                    await asyncio.shield(self._remove_owned(name))
                    result["cleanup_confirmed"] = True
                except (DockerUnavailable, DockerExecutionError, TimeoutError):
                    result["cleanup_confirmed"] = False
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                if process.returncode is None:
                    process.kill()
                await process.wait()
                if result["cleanup_confirmed"]:
                    self._active.discard(name)
                result.update(stdout=stdout.decode(errors="replace"), stderr=stderr.decode(errors="replace"),
                              inspections=self.evidence[offset:], image_digest=self.image,
                              cli_exit_code=process.returncode)
                self.evidence.append({"operation": "run_finished", **result})
        return result

    async def check(
        self, document: dict[str, Any], rules: list[dict[str, Any]], *,
        task_id: str, attempt_id: str, fence: int, timeout_seconds: float = 10,
        timeout_probe: bool = False,
        on_started: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        UUID(task_id)
        UUID(attempt_id)
        if timeout_probe and not self.test_mode:
            raise ValueError("fault switches require trusted test mode")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Docker wall clock limit must be in (0, 30]")
        available = await self.availability()
        if not available["available"]:
            raise DockerUnavailable(str(available["reason"]))
        payload = json.dumps({"document": document, "rules": rules}).encode()
        if len(payload) > 64 * 1024:
            raise ValueError("tool input exceeds 64 KiB")
        name = CONTAINER_PREFIX + re.sub("-", "", task_id) + "-" + str(fence)
        script = TIMEOUT_SCRIPT if timeout_probe else CHECK_SCRIPT
        with tempfile.TemporaryDirectory(prefix="arc-ref-docker-") as config_dir:
            process = await asyncio.create_subprocess_exec(
                "docker", "--config", config_dir, *self.command(name, attempt_id, fence, script, task_id),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.defpath, "DOCKER_HOST": "unix:///var/run/docker.sock"},
            )
            self._active.add(name)
            try:
                async with asyncio.timeout(timeout_seconds):
                    if timeout_probe:
                        assert process.stdout is not None
                        line = await process.stdout.readline()
                        if line.strip() != b"ARC_REF_STARTED":
                            raise DockerExecutionError("timeout probe failed before startup barrier")
                        if on_started:
                            await on_started(name)
                    stdout, _ = await process.communicate(payload)
                    if process.returncode:
                        raise DockerExecutionError("container tool execution failed")
                    result: dict[str, Any] = json.loads(stdout)
                    return result
            finally:
                # kill Docker CLI 不等于停止容器，所以先按所有权删除容器再回收CLI。
                try:
                    await asyncio.shield(self._remove_owned(name))
                finally:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    self._active.discard(name)
