"""Repeat existing real-process probes. Never relabel partial workflows as end-to-end."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from config.settings import settings
from examples.demo import cancellation_order, security
from examples.process_scenarios import crash_checkpoint, external_effect, lease_competition
from runtime.storage.pool import close_pools
from runtime.storage.store import Store


async def run(args):
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "reliability"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "raw.jsonl"
    if path.exists():
        raise RuntimeError("refusing to overwrite existing experiment; choose a new run_id")
    plan = [
        ("external_idempotent", 10, lambda: external_effect("idempotent")),
        ("external_queryable", 10, lambda: external_effect("queryable")),
        ("external_unsafe", 10, lambda: external_effect("unsafe")),
        ("old_worker", 30, lease_competition),
        ("checkpoint", 10, crash_checkpoint),
        ("cancel_order", 10, cancellation_order),
        ("audit_security", 10, lambda: security(out)),
    ]
    (out / "config.json").write_text(
        json.dumps(
            {
                "scope": "deterministic runtime probes, not model repair evaluation",
                "planned": {name: n for name, n, _ in plan},
                "full_count": not args.smoke,
            },
            indent=2,
        )
    )
    try:
        for name, count, scenario in plan:
            for repeat in range(1, (1 if args.smoke else count) + 1):
                # Explicit admission-window wait, unrelated to fault injection timing.
                store = Store(settings.database_url.get_secret_value())
                needed = 3 if name == "audit_security" else (2 if name == "cancel_order" else 1)
                async with store.connection() as conn:
                    admission = await (
                        await conn.execute(
                            "SELECT requests,EXTRACT(EPOCH FROM clock_timestamp()-window_start) AS age FROM arc_ref.admissions WHERE principal_id='demo-reviewer'"
                        )
                    ).fetchone()
                admission_wait = 0.0
                if admission and admission["requests"] + needed > 30 and admission["age"] < 60:
                    admission_wait = 60 - float(admission["age"]) + 0.05
                    await asyncio.sleep(admission_wait)
                tick = time.monotonic()
                try:
                    result = await scenario()
                    record = {
                        "scenario": name,
                        "repeat": repeat,
                        "elapsed_seconds": time.monotonic() - tick,
                        "admission_wait_seconds": admission_wait,
                        "result": result,
                    }
                except Exception as exc:
                    record = {
                        "scenario": name,
                        "repeat": repeat,
                        "elapsed_seconds": time.monotonic() - tick,
                        "admission_wait_seconds": admission_wait,
                        "error_type": type(exc).__name__,
                        "status": "FAIL",
                    }
                    with path.open("a") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    raise
                with path.open("a") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(
                    json.dumps(
                        {
                            "scenario": name,
                            "repeat": repeat,
                            "status": result.get("status", "unknown"),
                        }
                    ),
                    flush=True,
                )
    finally:
        await close_pools()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    asyncio.run(run(parser.parse_args()))
