"""Admission process crash, free finalization race, and bad-summary evidence. No sandbox substitution."""

import asyncio
import copy
import json
import time
from pathlib import Path
from uuid import uuid4

from config.settings import settings
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.errors import RuntimeFault
from examples.process_scenarios import _barrier, _kill, _spawn, _wait_exit
from runtime.context.engine import ContextEngine
from runtime.storage.pool import close_pools
from runtime.storage.store import Store


async def run():
    out = (
        Path("reports")
        / json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))["run_id"]
        / "additional-faults"
    )
    out.mkdir(parents=True, exist_ok=True)
    path = out / "raw.jsonl"
    if path.exists():
        raise RuntimeError("choose new run_id; evidence already exists")
    store = Store(settings.database_url.get_secret_value())
    principal = Principal(principal_id="demo-reviewer")

    def append(row):
        with path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    async def admission_window():
        async with store.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT requests,EXTRACT(EPOCH FROM clock_timestamp()-window_start) AS age FROM arc_ref.admissions WHERE principal_id='demo-reviewer'"
                )
            ).fetchone()
        if row and row["requests"] >= 30 and row["age"] < 60:
            await asyncio.sleep(60 - float(row["age"]) + 0.05)

    try:
        for i in range(10):
            await admission_window()
            identifier = str(uuid4())
            child, log = await _spawn("tests.fakes.admission_probe", identifier)
            try:
                mark = await _barrier(store, "admission:" + identifier, child)
                task_id = mark["data"]["task_id"]
                before = await store.diagnostics(task_id)
                assert before["task"]["status"] == "QUEUED" and before["attempts"] == []
                await _kill(child)
                same = await store.submit(
                    principal, TaskRequest(document_id="DOC-002", session_id=identifier), identifier
                )
                assert same["task_id"] == task_id
                worker, log2 = await _spawn(
                    "runtime.orchestrator.worker", "--once", "--task-id", task_id
                )
                try:
                    await _wait_exit(worker, log2)
                finally:
                    await _kill(worker)
                after = await store.diagnostics(task_id)
                assert len(after["attempts"]) == 1 and after["task"]["checkpoint"] >= 0
                assert (
                    after["task"]["status"] == "SUCCEEDED"
                    or after["task"]["error_code"] == "DOCKER_UNAVAILABLE"
                )
                append(
                    {
                        "scenario": "admission_before_claim",
                        "repeat": i + 1,
                        "status": "passed",
                        "scope": "durable admission recovery only",
                        "full_report": "PASS"
                        if after["task"]["status"] == "SUCCEEDED"
                        else "BLOCKED",
                        "task_id": task_id,
                        "before": before,
                        "after": after,
                        "first_exit": child.returncode,
                        "logs": [log, log2],
                    }
                )
            finally:
                await _kill(child)
        for i in range(30):
            await admission_window()
            task = await store.submit(
                principal, TaskRequest(document_id="DOC-001", session_id=str(uuid4())), str(uuid4())
            )
            ex = await store.claim("free-finalization-race", 30, task["task_id"])
            assert ex
            start = asyncio.Event()

            async def cancel(start=start, ex=ex):
                await start.wait()
                await store.cancel(ex.task_id, principal.principal_id)
                return "cancel_committed"

            async def finish(start=start, ex=ex):
                await start.wait()
                try:
                    await store.finish(ex, {"probe_only": "free-race-no-business-report"})
                    return "finish_committed"
                except RuntimeFault as exc:
                    assert exc.code == "CANCELLED"
                    await store.fail(ex, "CANCELLED")
                    return "finish_rejected_cancelled"

            tick = time.monotonic()
            jobs = [
                asyncio.create_task(fn())
                for fn in ((cancel, finish) if i % 2 else (finish, cancel))
            ]
            start.set()
            outcomes = await asyncio.gather(*jobs)
            diag = await store.diagnostics(ex.task_id)
            expected = "SUCCEEDED" if "finish_committed" in outcomes else "CANCELLED"
            assert diag["task"]["status"] == expected
            terminals = [
                row for row in diag["audit"] if row["event"] in {"task.succeeded", "task.cancelled"}
            ]
            assert len(terminals) == 1
            append(
                {
                    "scenario": "free_cancel_finish_race",
                    "repeat": i + 1,
                    "status": "passed",
                    "scope": "two concurrent PG transactions, synthetic finish result, no candidate execution",
                    "elapsed_seconds": time.monotonic() - tick,
                    "task_id": ex.task_id,
                    "attempt_id": ex.attempt_id,
                    "outcomes": outcomes,
                    "diagnostics": diag,
                }
            )
        facts = {
            "base_hash": "a" * 64,
            "test_suite_hash": "b" * 64,
            "candidate": {"no": 1, "hash": "c" * 64},
            "deadline": "fixed",
            "pending_effect": "call-1",
        }
        engine = ContextEngine(store)
        for kind in facts:
            for repeat in (1, 2):
                context = {"protected_facts": copy.deepcopy(facts)}
                bad = copy.deepcopy(context)
                bad["protected_facts"].pop(kind)
                try:
                    engine.apply_summary(context, bad)
                    raise AssertionError("bad summary accepted")
                except RuntimeFault as exc:
                    assert (
                        exc.code == "PROTECTED_FACTS_LOST" and context["protected_facts"] == facts
                    )
                append(
                    {
                        "scenario": "bad_summary",
                        "kind": kind,
                        "repeat": repeat,
                        "status": "passed",
                        "scope": "pure data contract, synthetic facts",
                    }
                )
    finally:
        await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
