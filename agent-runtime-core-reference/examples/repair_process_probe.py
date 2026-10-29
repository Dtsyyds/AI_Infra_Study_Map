"""Independent processes proving committed patch recovery, not sandbox repair success."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from examples.process_scenarios import _barrier, _kill, _spawn, _wait_exit, _wait_expired
from runtime.storage.pool import close_pools
from runtime.storage.store import Store


async def run():
    store = Store(settings.database_url.get_secret_value())
    principal = Principal(principal_id="repair-process-" + str(uuid4()))
    task = await store.submit(
        principal,
        TaskRequest(task_type="code_repair", case_id="case-01", session_id=str(uuid4())),
        str(uuid4()),
    )
    task_id = task["task_id"]
    async with store.connection() as conn:
        await conn.execute(
            "UPDATE arc_ref.tasks SET context=jsonb_set(context,'{baseline}',%s) WHERE task_id=%s",
            (
                Jsonb(
                    {
                        "verdict": {"status": "FAIL", "failed_ids": ["test_case.Case.test_bug"]},
                        "scope": "synthetic fixture",
                    }
                ),
                task_id,
            ),
        )
    processes = []
    try:
        first, log1 = await _spawn("tests.fakes.repair_checkpoint_probe", task_id, "--first")
        processes.append(first)
        await _barrier(store, "repair-patch:" + task_id, first)
        before = await store.diagnostics(task_id)
        await _kill(first)
        await _wait_expired(store, task_id)
        second, log2 = await _spawn("tests.fakes.repair_checkpoint_probe", task_id)
        processes.append(second)
        await _wait_exit(second, log2)
        after = await store.diagnostics(task_id)
        assert len(after["attempts"]) == 2 and after["task"]["checkpoint"] == 0
        assert before["repair_candidates"] == after["repair_candidates"]
        assert before["steps"] == after["steps"]
        assert after["task"]["result"]["business_outcome"] == "UNRESOLVED"
        content = await store.artifact(
            task_id, after["artifacts"][0]["artifact_id"], principal.principal_id
        )
        state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
        out = Path("reports") / state["run_id"] / "repair-process"
        out.mkdir(parents=True, exist_ok=True)
        (out / "patch-storage-fixture.json").write_bytes(content)
        result = {
            "scope": "persistence-only synthetic fixture, not real baseline/candidate execution",
            "task_id": task_id,
            "attempt_ids": [a["attempt_id"] for a in after["attempts"]],
            "candidate_no": 1,
            "tool_call_id": after["steps"][0]["tool_call_id"],
            "logs": [log1, log2],
            "first_exit_code": first.returncode,
            "replacement_exit_code": second.returncode,
            "candidate_unchanged": True,
            "committed_step_not_repeated": True,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
            "before": before,
            "after": after,
        }
        (out / "result.json").write_text(json.dumps(result, indent=2))
        print(
            json.dumps({k: v for k, v in result.items() if k not in {"before", "after"}}, indent=2)
        )
    finally:
        for process in processes:
            await _kill(process)
        await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
