"""Real PostgreSQL lock/connection observations and a real Worker SIGTERM probe."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from uuid import uuid4

from config.settings import settings
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from examples.process_scenarios import (
    _barrier,
    _kill,
    _spawn,
    _wait_condition,
    _wait_exit,
    _wait_expired,
)
from runtime.storage.pool import close_pools, get_pool
from runtime.storage.store import Store


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "backend-probe.json"
    store = Store(settings.database_url.get_secret_value())
    principal = Principal(principal_id="backend-probe-" + str(uuid4()))
    task = await store.submit(
        principal, TaskRequest(document_id="DOC-001", session_id=str(uuid4())), str(uuid4())
    )
    result = {
        "task_id": task["task_id"],
        "scope": "local PG contention and non-container graceful stop",
    }
    try:
        async with (
            store.connection() as holder,
            store.connection() as waiter,
            store.connection() as observer,
        ):
            await holder.execute(
                "SELECT task_id FROM arc_ref.tasks WHERE task_id=%s FOR UPDATE", (task["task_id"],)
            )
            pid_row = await (await waiter.execute("SELECT pg_backend_pid() AS pid")).fetchone()
            assert pid_row is not None
            pid = pid_row["pid"]
            tick = time.monotonic()
            pending = asyncio.create_task(
                waiter.execute(
                    "SELECT task_id FROM arc_ref.tasks WHERE task_id=%s FOR UPDATE",
                    (task["task_id"],),
                )
            )

            async def blocked():
                row = await (
                    await observer.execute(
                        "SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE pid=%s",
                        (pid,),
                    )
                ).fetchone()
                return row if row and row["wait_event_type"] == "Lock" else None

            try:
                lock = await _wait_condition(blocked, 5)
                result["observed_lock_wait"] = lock
                result["pool_during_wait"] = (await get_pool(store.dsn)).get_stats()
                assert lock["blockers"] and result["pool_during_wait"]["pool_size"] <= 4
                await holder.rollback()
                await asyncio.wait_for(pending, 5)
                result["wait_until_release_seconds"] = time.monotonic() - tick
                await waiter.rollback()
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        worker, log = await _spawn(
            "runtime.orchestrator.worker",
            "--once",
            "--task-id",
            task["task_id"],
            ARC_TEST_BARRIER="before_dispatch:0",
        )
        try:
            await _barrier(store, "before_dispatch:0:" + task["task_id"], worker)
            before = await store.diagnostics(task["task_id"])
            tick = time.monotonic()
            os.killpg(worker.pid, signal.SIGTERM)
            await _wait_exit(worker, log)
            after = await store.diagnostics(task["task_id"])
            assert before["task"]["checkpoint"] == after["task"]["checkpoint"] == -1
            assert after["steps"] == [] and after["ledger"][0]["dispatch_count"] == 0
            assert len(after["attempts"]) == 1
            result["graceful_stop"] = {
                "exit_code": worker.returncode,
                "elapsed_seconds": time.monotonic() - tick,
                "log": log,
                "before": before,
                "after": after,
            }
            await store.cancel(task["task_id"], principal.principal_id)
            await _wait_expired(store, task["task_id"])
            await store.claim("probe-cleanup", 3, task["task_id"])
            result["final_task_status"] = (await store.snapshot(task["task_id"]))["status"]
            assert result["final_task_status"] == "CANCELLED"
        finally:
            await _kill(worker)
        result["status"] = "PASS"
    finally:
        await close_pools()
        out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "graceful_stop"}, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
