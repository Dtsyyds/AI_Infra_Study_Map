"""Real Fake-planner repair recovery; no baseline injection or host candidate execution."""

import asyncio
import json
import os
import signal
from pathlib import Path
from uuid import uuid4

from config.settings import settings
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from examples.process_scenarios import _barrier, _kill, _spawn, _wait_exit, _wait_expired
from runtime.storage.pool import close_pools
from runtime.storage.store import Store
from runtime.tools.docker_backend import DockerBackend


async def scenario(point):
    if point not in {"patch_committed", "verification_committed", "test_running", "graceful_stop"}:
        raise ValueError("unknown barrier")
    store = Store(settings.database_url.get_secret_value())
    # Exercise the actual worker policy; unique session/task IDs isolate each probe.
    p = Principal(principal_id="demo-reviewer")
    task = await store.submit(
        p,
        TaskRequest(
            task_type="code_repair",
            case_id="case-01",
            session_id=str(uuid4()),
            repair_strategy="single",
            budget_ms=120000,
        ),
        str(uuid4()),
    )
    task_id = task["task_id"]
    switch = {
        "patch_committed": "after_checkpoint:1",
        "verification_committed": "after_checkpoint:2",
        "test_running": "repair_running",
        "graceful_stop": "repair_running",
    }[point]
    child, log1 = await _spawn(
        "runtime.orchestrator.worker", "--once", "--task-id", task_id, ARC_TEST_BARRIER=switch
    )
    logs = [log1]
    processes = [child]
    try:
        await _barrier(store, switch + ":" + task_id, child)
        before = await store.diagnostics(task_id)
        assert len(before["repair_candidates"]) == 1 and before["model_calls"] == []
        graceful = None
        if point == "graceful_stop":
            backend = DockerBackend()
            pending = next(row for row in before["repair_verifications"] if row["candidate_no"] == 1)
            name = f"arc-ref-tool-{task_id}-{before['task']['fence']}-{pending['verification_run_id']}"
            running = await backend.inspect_state(name)
            assert running["status"] == "RUNNING"
            os.kill(child.pid, signal.SIGTERM)
            await _wait_exit(child, log1)
            absent = await backend.inspect_state(name)
            assert absent["status"] == "ABSENT"
            graceful = {"before_signal": running, "after_exit": absent, "exit_code": child.returncode}
        else:
            await _kill(child)
        await _wait_expired(store, task_id)
        new, log2 = await _spawn("runtime.orchestrator.worker", "--once", "--task-id", task_id)
        processes.append(new)
        logs.append(log2)
        await _wait_exit(new, log2)
        after = await store.diagnostics(task_id)
        assert len(after["attempts"]) == 2 and len(after["repair_candidates"]) == 1
        assert (
            after["task"]["status"] == "SUCCEEDED"
            and after["task"]["result"]["business_outcome"] == "UNRESOLVED"
        )
        assert after["model_calls"] == []
        assert before["repair_candidates"][0]["patch"] == after["repair_candidates"][0]["patch"]
        for step in before["steps"]:
            assert step in after["steps"]
            old = next(
                row for row in before["ledger"] if row["tool_call_id"] == step["tool_call_id"]
            )
            current = next(
                row for row in after["ledger"] if row["tool_call_id"] == step["tool_call_id"]
            )
            assert old["dispatch_count"] == current["dispatch_count"]
        if point in {"test_running", "graceful_stop"}:
            old = next(row for row in before["repair_verifications"] if row["candidate_no"] == 1)
            runs = [row for row in after["repair_verifications"] if row["candidate_no"] == 1]
            assert len(runs) == 2 and len({r["verification_run_id"] for r in runs}) == 2
            assert all(r["tool_call_id"] == old["tool_call_id"] for r in runs)
            assert sum(r["result"] is not None for r in runs) == 1
            cleanup = [
                line.removeprefix("ARC_REF_STARTUP_CLEANUP ")
                for line in (await asyncio.to_thread(Path(log2).read_text)).splitlines()
                if line.startswith("ARC_REF_STARTUP_CLEANUP ")
            ]
            assert cleanup
            if point == "test_running":
                assert json.loads(cleanup[0])["removed"]
            else:
                assert not json.loads(cleanup[0])["removed"]
        return {
            "status": "PASS",
            "scope": "real sandbox, deterministic Fake repair, never model success",
            "point": point,
            "task_id": task_id,
            "logs": logs,
            "before": before,
            "after": after,
            "first_exit_code": child.returncode,
            "replacement_exit_code": new.returncode,
            "graceful_stop": graceful,
        }
    finally:
        for process in processes:
            await _kill(process)
        snapshot = await store.snapshot(task_id)
        if snapshot["status"] in {"RUNNING", "QUEUED"}:
            await store.cancel(task_id, p.principal_id)
            if snapshot["status"] == "RUNNING":
                await _wait_expired(store, task_id)
            await store.claim("recovery-probe-cleanup", 3, task_id)


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "repair-recovery"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.jsonl"
    if path.exists():
        raise RuntimeError("new run_id required")
    available = await DockerBackend().availability()
    try:
        for point in ("patch_committed", "verification_committed", "test_running", "graceful_stop"):
            if not available["available"]:
                row = {
                    "point": point,
                    "status": "BLOCKED",
                    "reason": available["reason"],
                    "executed": False,
                }
            else:
                try:
                    row = await scenario(point)
                except Exception as exc:
                    row = {"point": point, "status": "FAIL", "error_type": type(exc).__name__}
                    with path.open("a") as stream:
                        stream.write(json.dumps(row) + "\n")
                    raise
            with path.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(json.dumps({k: v for k, v in row.items() if k not in {"before", "after"}}))
    finally:
        await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
