"""Docker 不可用时显式 BLOCKED skip；不在宿主执行脚本冒充隔离测试。"""

import asyncio
from uuid import uuid4

import pytest

from runtime.tools.docker_backend import IMAGE, DockerBackend

RULES = [
    {"rule_id": "RULE-APPROVAL", "version": "1", "rule_type": "approval_threshold",
     "threshold": 10000, "source": "fictional://policy/approval"},
    {"rule_id": "RULE-INVOICE", "version": "1", "rule_type": "invoice_required",
     "source": "fictional://policy/invoice"},
]


async def require_real_docker(backend: DockerBackend) -> None:
    available = await backend.availability()
    if not available["available"]:
        pytest.skip("BLOCKED: " + available["reason"])
    code, _, _ = await backend._docker("image", "inspect", IMAGE)
    if code:
        pytest.skip("BLOCKED: pinned Docker image not installed; run make images")


def test_fixed_command_contains_isolation_and_no_host_credentials() -> None:
    command = DockerBackend().command("arc-ref-tool-unit", str(uuid4()), 1, "fixed script")
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "65534:65534"
    assert command[command.index("--memory") + 1] == "128m"
    assert command[command.index("--pids-limit") + 1] == "32"
    assert "--read-only" in command
    assert "no-new-privileges:true" in command
    assert "/var/run/docker.sock" not in str(command)
    assert "--volume" not in command and "--env" not in command
    assert IMAGE in command and "@sha256:" in IMAGE


@pytest.mark.asyncio
async def test_real_docker_checks_fields_and_cleans_container() -> None:
    backend = DockerBackend()
    await require_real_docker(backend)
    document = {"document_id": "DOC-901", "version": "1", "amount": 12000,
                "attachments": ["invoice"], "source": "fictional://test/DOC-901"}
    task_id, attempt_id = str(uuid4()), str(uuid4())
    result = await backend.check(document, RULES, task_id=task_id, attempt_id=attempt_id, fence=1)
    assert result["review_decision"] == "NEEDS_DOCUMENTS"
    assert result["missing_documents"] == ["approval"]
    document["amount"] = 8000
    changed = await backend.check(document, RULES, task_id=task_id, attempt_id=attempt_id, fence=2)
    assert changed["review_decision"] == "PASS"
    assert changed["missing_documents"] == []
    assert not backend._active


@pytest.mark.asyncio
async def test_real_timeout_reaches_process_barrier_then_container_is_gone() -> None:
    backend = DockerBackend(test_mode=True)
    await require_real_docker(backend)
    started = asyncio.Event()
    names = []

    async def on_started(name: str) -> None:
        record = await backend._inspect_owned(name)
        assert record is not None and record["State"]["Running"]
        names.append(name)
        started.set()

    with pytest.raises(TimeoutError):
        await backend.check(
            {}, [], task_id=str(uuid4()), attempt_id=str(uuid4()), fence=1,
            timeout_seconds=2, timeout_probe=True, on_started=on_started,
        )
    assert started.is_set(), "timeout before startup is not evidence that a running process was killed"
    assert await backend._inspect_owned(names[0]) is None


@pytest.mark.asyncio
async def test_test_switch_is_not_available_to_normal_backend() -> None:
    with pytest.raises(ValueError, match="test mode"):
        await DockerBackend().check(
            {}, [], task_id=str(uuid4()), attempt_id=str(uuid4()), fence=1, timeout_probe=True,
        )
