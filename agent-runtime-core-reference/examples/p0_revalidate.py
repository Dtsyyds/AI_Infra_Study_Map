"""Real A/C/F evidence; BLOCKED is never promoted to PASS."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from examples.demo import normal
from examples.process_scenarios import crash_checkpoint
from runtime.storage.store import Store, digest
from runtime.tools.docker_backend import DockerBackend


async def run():
    run_id = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))["run_id"]
    directory = Path("reports") / run_id / "p0"
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_url.get_secret_value())
    result = {
        "run_id": run_id,
        "docker": await DockerBackend().availability(),
        "overall": "BLOCKED",
    }
    try:
        result["A"] = await normal(directory)
        result["C"] = await crash_checkpoint()
        tasks = [t["task_id"] for t in result["A"]["tasks"]] + [result["C"]["task_id"]]
        exported = []
        for task_id in tasks:
            diag = await store.diagnostics(task_id)
            artifacts = []
            for artifact in diag["artifacts"]:
                content = await store.artifact(
                    task_id, artifact["artifact_id"], diag["task"]["principal_id"]
                )
                filename = artifact["artifact_id"] + ".json"
                (directory / filename).write_bytes(content)
                artifacts.append(
                    {**artifact, "sha256": hashlib.sha256(content).hexdigest(), "file": filename}
                )
            (directory / (task_id + ".json")).write_text(
                json.dumps(diag, ensure_ascii=False, indent=2)
            )
            log = directory / f"worker-{task_id}.log"
            exported.append(
                {
                    "task_id": task_id,
                    "attempt_ids": [a["attempt_id"] for a in diag["attempts"]],
                    "tool_call_ids": [a["tool_call_id"] for a in diag["actions"]],
                    "artifacts": artifacts,
                    "mcp_read_count": sum(
                        line.startswith("ARC_REF_DOCUMENT_READ ")
                        for line in log.read_text().splitlines()
                    )
                    if log.exists()
                    else None,
                }
            )
        result["identities"] = exported
        backend = DockerBackend(test_mode=True)
        if not result["docker"]["available"]:
            result["F"] = {
                "status": "BLOCKED",
                "reason": "DOCKER_UNAVAILABLE",
                "started": False,
                "cleanup": "UNKNOWN",
            }
        else:
            markers = []
            principal = Principal(principal_id="p0-timeout-" + str(uuid4()))
            task = await store.submit(principal, TaskRequest(document_id="DOC-002", session_id=str(uuid4())), str(uuid4()))
            execution = await store.claim("p0-timeout-controller", 30, task["task_id"])
            assert execution is not None
            document, rules = await store.document("DOC-002"), await store.rules()
            arguments = {"document": document, "rules": rules}
            action = PreparedAction(task_id=execution.task_id, step_no=0, tool_call_id=str(uuid4()),
                                    tool="document.check", arguments=arguments,
                                    arguments_digest=digest(arguments), effect_class=EffectClass.READ_ONLY)
            await store.prepare(execution, action)
            await store.dispatch(execution, action)

            async def started(name):
                record = await backend._inspect_owned(name)
                assert record and record["State"]["Running"]
                markers.append({"marker": "ARC_REF_STARTED", "container": record})

            try:
                await backend.check(
                    document,
                    rules,
                    task_id=execution.task_id,
                    attempt_id=execution.attempt_id,
                    fence=execution.fence,
                    timeout_seconds=2,
                    timeout_probe=True,
                    on_started=started,
                )
                raise AssertionError("timeout expected")
            except TimeoutError:
                assert markers
            finally:
                await store.fail(execution, "TOOL_TIMEOUT_PROBE")
            result["F"] = {
                "status": "PASS",
                "started": markers,
                "inspections": backend.evidence,
                "task_id": execution.task_id,
                "attempt_id": execution.attempt_id,
                "tool_call_id": action.tool_call_id,
                "diagnostics": await store.diagnostics(execution.task_id),
                "scope": "persisted tool timeout probe; not document_review completion",
            }
        if (
            result["A"]["status"] == "passed"
            and result["C"]["status"] == "passed"
            and result["F"]["status"] == "PASS"
        ):
            result["overall"] = "PASS"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["overall"] = "FAIL"
        raise
    finally:
        (directory / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
