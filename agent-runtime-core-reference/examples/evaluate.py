"""Pre-register model/load experiment slots; execution requires explicit --execute."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from config.settings import settings
from examples.experiment_driver import submit_observe
from examples.experiment_metrics import summarize_experiment
from examples.harness_acceptance import qualified
from runtime.planning.adapters import PROMPT_VERSION
from runtime.repair.manifest import canonical, load_case, sha
from runtime.tools.docker_backend import DockerBackend


def slots():
    return [
        {"case_id": f"case-{case:02}", "group": group, "round": repeat, "strategy": strategy}
        for group, strategy in [("A", "single"), ("B", "context"), ("C", "loop")]
        for repeat in range(1, 4)
        for case in range(5, 13)
    ]


async def run(execute):
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in (
        "model_config.json", "model_raw.jsonl", "model_raw_events.jsonl", "not_run.json",
    )):
        raise RuntimeError("choose new run_id; no model evidence overwrite")
    docker = await DockerBackend().availability()
    authorized = (
        settings.model_enabled
        and settings.model_request_limit > 0
        and settings.model_token_limit > 0
        and bool(settings.model_name and settings.model_budget_id)
    )
    reasons = []
    if not docker["available"]:
        reasons.append("DOCKER_UNAVAILABLE")
    if not authorized:
        reasons.append("MODEL_AUTHORIZATION_OR_BUDGET_MISSING")
    if not await asyncio.to_thread(qualified, out.parent / "harness-acceptance"):
        reasons.append("CURRENT_HARNESS_QUALIFICATION_MISSING")
    if not execute:
        reasons.append("EXECUTION_NOT_REQUESTED")
    plan = {
        "model": settings.model_name or None,
        "model_requests_limit": settings.model_request_limit,
        "model_tokens_limit": settings.model_token_limit,
        "fake_results_allowed": False,
        "prompt_version": PROMPT_VERSION,
        "temperature": settings.model_temperature,
        "max_output_tokens": settings.model_max_output_tokens,
        "context_byte_cap_both_A_B": 24000,
        "per_task_candidate_limit": {"A": 1, "B": 1, "C": 3},
        "manifests": {
            f"case-{i:02}": sha(canonical(load_case(f"case-{i:02}")[0].model_dump()))
            for i in range(5, 13)
        },
        "planned_real_tasks": slots(),
        "planned_load": {
            "concurrency": [1, 5, 10],
            "rounds": 3,
            "tasks_per_round": 30,
            "total": 270,
        },
        "load_status": "SEPARATE_EXPERIMENT",
        "load_evidence_dir": state.get("load_evidence_dir", "benchmarks"),
        "load_reason": "this model driver does not determine load execution status or counts",
        "blocking_reasons": reasons,
        "model_observed_tasks": 0,
    }
    if reasons:
        (out / "not_run.json").write_text(json.dumps(plan, indent=2))
        print(
            json.dumps(
                {
                    "planned_model_tasks": 72,
                    "actual_model_tasks": 0,
                    "planned_load_tasks": 270,
                    "load_status": "SEPARATE_EXPERIMENT",
                    "reasons": reasons,
                }
            )
        )
        return
    raw = out / "model_raw.jsonl"
    (out / "model_config.json").write_text(json.dumps(plan, indent=2))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18081",
        trust_env=False,
        headers={"Authorization": "Bearer " + settings.user_token.get_secret_value()},
    ) as client:
        for slot in slots():
            record = await submit_observe(
                client,
                out,
                {
                    "task_type": "code_repair",
                    "case_id": slot["case_id"],
                    "planner_mode": "real",
                    "repair_strategy": slot["strategy"],
                    "budget_ms": 120000,
                },
                slot,
                model=True,
                record_path=raw,
            )
            plan["model_observed_tasks"] += 1
            (out / "model_config.json").write_text(json.dumps(plan, indent=2))
            if record.get("model_accounting", {}).get("errors"):
                break
            error = record.get("diagnostics", {}).get("task", {}).get("error_code")
            if error in {
                "MODEL_BLOCKED",
                "MODEL_BUDGET_EXHAUSTED",
                "REPAIR_GENERATION_FAILED",
                "BASELINE_NOT_CONFIRMED",
            } or record["status"] in {"REJECTED", "HTTP_ERROR", "OBSERVATION_ERROR"}:
                break
    (out / "model_config.json").write_text(json.dumps(plan, indent=2))
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    observed = {(r["case_id"], r["group"], r["round"]) for r in rows}
    (out / "model_missing.json").write_text(
        json.dumps(
            [s for s in slots() if (s["case_id"], s["group"], s["round"]) not in observed], indent=2
        )
    )
    (out / "model_summary.json").write_text(
        json.dumps(summarize_experiment(rows, kind="model", planned=72), indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    asyncio.run(run(parser.parse_args().execute))
