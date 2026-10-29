"""In-memory transport protocol only; never executes code or claims sandbox validation."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from runtime.tools.docker_backend import DockerBackend, DockerUnavailable


class Process:
    def __init__(self, output):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = self
        self.returncode = None
        self.finished = asyncio.Event()

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    def kill(self):
        self.returncode = -9
        self.stdout.feed_eof()
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        return self.returncode


@pytest.mark.parametrize("cleanup", [True, False])
async def test_cancel_records_cleanup_and_releases_transport_without_claiming_real_container(
    monkeypatch, cleanup
):
    backend = DockerBackend()
    backend.availability = AsyncMock(return_value={"available": True})
    backend._remove_owned = AsyncMock(side_effect=None if cleanup else DockerUnavailable("unknown"))
    proc = Process(b"ARC_REPAIR_STARTED\nARC_REPAIR_TEST_ENTERED\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    started = asyncio.Event()

    async def marker(name):
        started.set()

    operation = asyncio.create_task(
        backend.run_fixed(
            "never executed",
            b"",
            task_id=str(uuid4()),
            attempt_id=str(uuid4()),
            run_id=str(uuid4()),
            fence=1,
            wall_seconds=5,
            output_bytes=1024,
            on_test_started=marker,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    evidence = backend.evidence[-1]
    assert evidence["operation"] == "run_finished" and evidence["cancelled"]
    assert evidence["cleanup_confirmed"] is cleanup and proc.returncode == -9
    assert bool(backend._active) is (not cleanup)
    assert "ARC_REPAIR_TEST_ENTERED" in evidence["stdout"]


async def test_output_overflow_is_never_success(monkeypatch):
    backend = DockerBackend()
    backend.availability = AsyncMock(return_value={"available": True})
    backend._remove_owned = AsyncMock()
    proc = Process(b"x" * 2048)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    result = await backend.run_fixed(
        "never executed",
        b"",
        task_id=str(uuid4()),
        attempt_id=str(uuid4()),
        run_id=str(uuid4()),
        fence=1,
        wall_seconds=1,
        output_bytes=1024,
    )
    assert result["resource_error"] and result["cleanup_confirmed"] and result["exit_code"] is None
