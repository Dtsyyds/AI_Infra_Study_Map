"""Protocol/error tests only. These are not evidence of real container isolation."""

import json
from unittest.mock import AsyncMock

import pytest

from runtime.tools.docker_backend import DockerBackend, DockerUnavailable


@pytest.mark.parametrize(
    "error", ["permission denied", "Cannot connect to daemon", "timeout", "No such container"]
)
async def test_unknown_is_never_absent(error):
    backend = DockerBackend()
    backend._docker = AsyncMock(return_value=(1, "", error))
    assert (await backend.inspect_state("example"))["status"] == "UNKNOWN"
    with pytest.raises(DockerUnavailable):
        await backend._inspect_owned("example")


async def test_absence_requires_healthy_daemon():
    backend = DockerBackend()
    backend._docker = AsyncMock(
        return_value=(1, "", "Error response from daemon: No such container: example\n")
    )
    backend.availability = AsyncMock(return_value={"available": False})
    assert (await backend.inspect_state("example"))["status"] == "UNKNOWN"
    backend.availability.return_value = {"available": True}
    assert await backend._inspect_owned("example") is None


@pytest.mark.parametrize("payload", ["", "{", "[]", "[{}]", '{"State":{}}'])
async def test_broken_inspect_is_unknown(payload):
    backend = DockerBackend()
    backend._docker = AsyncMock(return_value=(0, payload, ""))
    assert (await backend.inspect_state("x"))["status"] == "UNKNOWN"


@pytest.mark.parametrize("running,state", [(True, "RUNNING"), (False, "STOPPED")])
async def test_stopped_is_not_absent(running, state):
    backend = DockerBackend()
    record = {"Id": "id", "Name": "/arc-ref-tool-test", "Config": {}, "State": {"Running": running}}
    backend._docker = AsyncMock(return_value=(0, json.dumps([record]), ""))
    assert (await backend.inspect_state("id"))["status"] == state


async def test_timeout_is_unknown():
    backend = DockerBackend()
    backend._docker = AsyncMock(side_effect=TimeoutError)
    with pytest.raises(DockerUnavailable):
        await backend._remove_owned("id")


async def test_unknown_after_removal_is_not_cleanup_success():
    from runtime.tools.docker_backend import DockerExecutionError

    backend = DockerBackend()
    backend._inspect_owned = AsyncMock(return_value={"Id": "id", "State": {"Running": False}})
    backend._docker = AsyncMock(return_value=(0, "", ""))
    backend.inspect_state = AsyncMock(return_value={"status": "UNKNOWN"})
    with pytest.raises(DockerExecutionError, match="unconfirmed"):
        await backend._remove_owned("id")


async def test_orphan_check_uses_task_attempt_fence_and_fails_closed():
    from uuid import uuid4

    from runtime.tools.docker_backend import ATTEMPT_LABEL, FENCE_LABEL, TASK_LABEL

    backend = DockerBackend()
    task, attempt = str(uuid4()), str(uuid4())
    backend._docker = AsyncMock(return_value=(0, "container\n", ""))
    backend._inspect_owned = AsyncMock(
        return_value={
            "Id": "container",
            "Config": {"Labels": {TASK_LABEL: task, ATTEMPT_LABEL: attempt, FENCE_LABEL: "7"}},
        }
    )
    backend._remove_owned = AsyncMock()
    active = AsyncMock(return_value=True)
    assert await backend.cleanup_orphans(active) == []
    active.assert_awaited_once_with(task, attempt, 7)
    backend._remove_owned.assert_not_awaited()
    active.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await backend.cleanup_orphans(active)
    backend._remove_owned.assert_not_awaited()
    active.side_effect = None
    active.return_value = False
    assert await backend.cleanup_orphans(active) == ["container"]


async def test_unknown_platform_ownership_is_retained():
    from uuid import uuid4

    from runtime.tools.docker_backend import ATTEMPT_LABEL, FENCE_LABEL, TASK_LABEL

    backend = DockerBackend()
    backend._docker = AsyncMock(return_value=(0, "container\n", ""))
    backend._inspect_owned = AsyncMock(return_value={
        "Id": "container", "Config": {"Labels": {
            TASK_LABEL: str(uuid4()), ATTEMPT_LABEL: str(uuid4()), FENCE_LABEL: "1"}}})
    backend._remove_owned = AsyncMock()
    assert await backend.cleanup_orphans(AsyncMock(return_value=None)) == []
    backend._remove_owned.assert_not_awaited()
    assert backend.evidence[-1]["active"] is None
