"""Representative real query plans over rollback-only synthetic reference data."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from config.settings import settings
from runtime.storage.pool import close_pools, get_pool
from runtime.storage.store import Store


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    store = Store(settings.database_url.get_secret_value())
    principal = "sql-probe-" + str(uuid4())
    plans = {}
    async with store.connection() as conn:
        async with conn.transaction(force_rollback=True):
            await conn.execute(
                "INSERT INTO arc_ref.sessions(principal_id,session_id) SELECT %s,'s-'||i FROM generate_series(1,1000) AS i",
                (principal,),
            )
            await conn.execute(
                "INSERT INTO arc_ref.tasks(task_id,principal_id,principal,session_id,request,request_digest,status,deadline_at) SELECT gen_random_uuid(),%s,'{}','s-'||i,'{}','synthetic',CASE WHEN i<=10 THEN 'QUEUED' ELSE 'SUCCEEDED' END,clock_timestamp()+interval '1 hour' FROM generate_series(1,1000) AS i",
                (principal,),
            )
            task = await store._one(
                conn,
                "SELECT task_id FROM arc_ref.tasks WHERE principal_id=%s LIMIT 1",
                (principal,),
            )
            queries = {
                "claim": (
                    "SELECT * FROM arc_ref.tasks WHERE status='QUEUED' OR (status='RUNNING' AND lease_until<=clock_timestamp()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
                    (),
                ),
                "pending_action": (
                    "SELECT action FROM arc_ref.actions WHERE task_id=%s AND step_no=%s",
                    (task["task_id"], 0),
                ),
                "diagnostics": ("SELECT * FROM arc_ref.steps WHERE task_id=%s", (task["task_id"],)),
            }
            for name, (query, args) in queries.items():
                cursor = await conn.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, args
                )
                row = await cursor.fetchone()
                assert row is not None
                plans[name] = {"query": query, "plan": row["QUERY PLAN"]}
        remaining = await store._one(
            conn, "SELECT count(*) AS n FROM arc_ref.tasks WHERE principal_id=%s", (principal,)
        )
        assert remaining["n"] == 0
    out = Path("reports") / state["run_id"] / "sql-plans.json"
    result = {
        "synthetic_inserted_rows": 1000,
        "synthetic_remaining_rows": 0,
        "statistics": "existing planner statistics; no global config changed",
        "scope": "single local sample; no optimization or production throughput claim",
        "plans": plans,
        "pool": (await get_pool(store.dsn)).get_stats(),
    }
    out.write_text(json.dumps(result, indent=2, default=str))
    print(out)
    await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
