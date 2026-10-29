"""270 unique submissions, explicit overload classifications; no 202-as-success shortcut."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
import psycopg

from config.settings import settings
from examples.experiment_driver import append_record, submit_observe
from examples.experiment_metrics import summarize_experiment
from runtime.tools.docker_backend import DockerBackend


async def wait_for_fresh_admission_window():
    """Read-only pacing between rounds; never reset or relax the real quota."""
    async with await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value()) as conn:
        row = await (await conn.execute(
            "SELECT EXTRACT(EPOCH FROM clock_timestamp()-window_start) FROM arc_ref.admissions WHERE principal_id='demo-reviewer'"
        )).fetchone()
    delay = max(0.0, 60.05 - float(row[0])) if row else 0.0
    if delay:
        await asyncio.sleep(delay)
    return delay


async def run(execute, attempt=1, profile="overload"):
    if attempt < 1:
        raise ValueError("attempt must be positive")
    if profile not in {"overload", "steady"}:
        raise ValueError("unknown load profile")
    concurrency_levels = [1, 2, 3] if profile == "steady" else [1, 5, 10]
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "benchmarks"
    if attempt > 1:
        out /= f"load-attempt-{attempt}"
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in (
        "load_config.json", "load_raw.jsonl", "load_raw_events.jsonl",
        "load_windows.jsonl", "load_not_run.json",
    )):
        raise RuntimeError("choose new run_id or attempt; no evidence overwrite")
    plan = {
        "kind": "deterministic_document_review_runtime",
        "profile": profile,
        "attempt": attempt,
        "concurrency": concurrency_levels,
        "rounds": 3,
        "submissions_per_round": 30,
        "total": 270,
        "planner": "deterministic",
        "admission_limit_per_principal": 3,
        "rate_limit_per_minute": 30,
        "round_pacing": "wait for a fresh admission window; quota is never reset",
        "workload": "closed loop: each client slot waits for terminal observation and artifact export before submitting again",
        "scope": "local process-per-task experiment, including worker startup; not production throughput",
        "note": "overload refusals are counted separately, never mixed into successful latency",
    }
    available = await DockerBackend().availability()
    if not execute or not available["available"]:
        plan.update(
            status="BLOCKED" if not available["available"] else "NOT_RUN", actual_submissions=0
        )
        (out / "load_not_run.json").write_text(json.dumps(plan, indent=2))
        print(json.dumps(plan))
        return
    (out / "load_config.json").write_text(json.dumps(plan, indent=2))
    raw = out / "load_raw.jsonl"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18081",
        trust_env=False,
        headers={"Authorization": "Bearer " + settings.user_token.get_secret_value()},
        timeout=10,
    ) as client:
        for concurrency in concurrency_levels:
            for repeat in range(1, 4):
                delay = await wait_for_fresh_admission_window()
                append_record(out / "load_windows.jsonl", {
                    "concurrency": concurrency, "round": repeat, "wait_seconds": delay,
                })
                semaphore = asyncio.Semaphore(concurrency)

                async def task(index, semaphore=semaphore, concurrency=concurrency, repeat=repeat):
                    async with semaphore:
                        await submit_observe(
                            client,
                            out,
                            {"document_id": "DOC-002", "budget_ms": 120000},
                            {"index": index, "concurrency": concurrency, "round": repeat},
                            record_path=raw,
                        )

                await asyncio.gather(*(task(i) for i in range(30)))
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    (out / "load_summary.json").write_text(
        json.dumps(summarize_experiment(rows, kind="load", planned=270), indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--profile", choices=["overload", "steady"], default="overload")
    args = parser.parse_args()
    asyncio.run(run(args.execute, args.attempt, args.profile))
