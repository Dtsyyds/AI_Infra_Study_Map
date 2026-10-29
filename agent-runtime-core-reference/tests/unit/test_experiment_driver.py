import asyncio
import hashlib
import json
import os
import select
import subprocess
import sys

import httpx
import pytest

from examples.experiment_driver import submit_observe


async def test_202_without_terminal_evidence_is_not_success(tmp_path, monkeypatch):
    async def no_worker(*args, **kwargs):
        pass
    monkeypatch.setattr("examples.experiment_driver.worker", no_worker)
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "task"})
        return httpx.Response(503, json={"error": "DB_BUSY"})
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        result = await submit_observe(client, tmp_path, {}, {"index": 0}, record_path=tmp_path / "raw.jsonl")
    assert result["http_status"] == 202 and result["status"] == "OBSERVATION_ERROR"
    assert result["task_id"] == "task" and result["observation_incomplete"]


async def test_submission_failure_still_has_unique_identity_and_raw_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("synthetic")
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        one = await submit_observe(client, tmp_path, {}, {})
        two = await submit_observe(client, tmp_path, {}, {})
    assert one["submission_id"] != two["submission_id"]
    assert one["status"] == "OBSERVATION_ERROR" and one["errors"] == ["ConnectError"]


@pytest.mark.parametrize("valid_hash", [True, False])
async def test_success_requires_database_artifact_hash(tmp_path, monkeypatch, valid_hash):
    async def no_worker(*args, **kwargs):
        pass
    monkeypatch.setattr("examples.experiment_driver.worker", no_worker)
    content = b'{"report":"complete"}'
    digest = hashlib.sha256(content).hexdigest()
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "task"})
        if request.url.path.endswith("/diagnostics"):
            return httpx.Response(200, json={
                "task": {"status": "SUCCEEDED", "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:02Z"},
                "attempts": [{"started_at": "2026-01-01T00:00:01Z"}],
                "artifacts": [{"artifact_id": "artifact", "bytes": len(content),
                               "sha256": digest if valid_hash else "0" * 64}],
            })
        return httpx.Response(200, content=content)
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        result = await submit_observe(client, tmp_path, {}, {"index": 0})
    assert result["status"] == ("SUCCEEDED" if valid_hash else "OBSERVATION_ERROR")
    if valid_hash:
        assert result["artifacts"][0]["sha256"] == digest
        assert (tmp_path / "artifact-artifact.json").read_bytes() == content
    else:
        assert not (tmp_path / "artifact-artifact.json").exists()


@pytest.mark.parametrize("accepted", [False, True])
async def test_cancellation_is_recorded_and_propagated(tmp_path, monkeypatch, accepted):
    reached = asyncio.Event()
    async def wait_worker(*args, **kwargs):
        reached.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("examples.experiment_driver.worker", wait_worker)
    async def handler(request):
        if accepted:
            return httpx.Response(202, json={"task_id": "task"})
        reached.set()
        await asyncio.Event().wait()
    raw = tmp_path / "raw.jsonl"
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        job = asyncio.create_task(submit_observe(client, tmp_path, {}, {}, record_path=raw))
        await asyncio.wait_for(reached.wait(), 2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    records = [json.loads(x) for x in raw.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["status"] == "OBSERVATION_ERROR"
    assert records[0]["errors"] == ["CancelledError"]
    assert records[0]["task_id"] == ("task" if accepted else None)
    events = [json.loads(x) for x in (tmp_path / "raw_events.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "SUBMISSION_INTENT"
    assert events[-1]["event"] == "OBSERVATION_FINISHED"
    assert len(events) == (3 if accepted else 2)
    assert events[0]["request"]["session_id"] == records[0]["submission_id"]


def test_sigkill_preserves_accepted_submission_identity(tmp_path):
    code = """
import asyncio, sys
from pathlib import Path
import httpx
from examples import experiment_driver as driver
async def worker(*args, **kwargs):
    print('ACCEPTED_AND_JOURNALED', flush=True)
    await asyncio.Event().wait()
driver.worker = worker
async def main():
    out = Path(sys.argv[1])
    async with httpx.AsyncClient(base_url='http://local', transport=httpx.MockTransport(
            lambda request: httpx.Response(202, json={'task_id':'persisted-task'}))) as client:
        await driver.submit_observe(client, out, {}, {'index': 7}, record_path=out/'raw.jsonl')
asyncio.run(main())
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        assert select.select([proc.stdout], [], [], 10)[0], "child did not reach durable barrier"
        assert proc.stdout.readline() == b"ACCEPTED_AND_JOURNALED\n"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)
    events = [json.loads(x) for x in (tmp_path / "raw_events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in events] == ["SUBMISSION_INTENT", "ACCEPTED"]
    assert events[0]["submission_id"] == events[1]["submission_id"]
    assert events[1]["task_id"] == "persisted-task"
    assert not (tmp_path / "raw.jsonl").exists()  # No invented terminal outcome after SIGKILL.


async def test_succeeded_without_artifacts_is_incomplete(tmp_path, monkeypatch):
    async def worker(*args, **kwargs):
        pass
    monkeypatch.setattr("examples.experiment_driver.worker", worker)
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "task"})
        return httpx.Response(200, json={"task": {
            "status": "SUCCEEDED", "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:02Z"}, "artifacts": []})
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        record = await submit_observe(client, tmp_path, {}, {})
    assert record["status"] == "OBSERVATION_ERROR" and record["runtime_status"] == "SUCCEEDED"
