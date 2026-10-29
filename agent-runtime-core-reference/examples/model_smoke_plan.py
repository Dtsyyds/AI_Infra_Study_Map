"""Offline single-dev experiment plan. Never reads a model key or sends model requests."""

import argparse
import json
from pathlib import Path

from core.contracts.repair import Patch
from runtime.planning.adapters import INSTRUCTION, PROMPT_VERSION
from runtime.repair.manifest import canonical, load_case, sha


def build_plan(case_id="case-01"):
    manifest, _ = load_case(case_id)
    if manifest.split != "dev":
        raise ValueError("SMOKE_REQUIRES_DEV_CASE")
    schema = Patch.model_json_schema()

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False or set(node.get("required", [])) != set(node.get("properties", {})):
                    raise ValueError("STRICT_SCHEMA_CONTRACT_MISMATCH")
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(schema)
    return {
        "status": "PREPARED_NOT_AUTHORIZED", "network_requests": 0, "real_model_tasks": 0,
        "case_id": case_id, "split": manifest.split, "planned_tasks": 1,
        "strategy": "loop", "max_candidates": 3,
        "manifest_sha256": sha(canonical(manifest.model_dump())),
        "image_digest": manifest.image_digest, "runner_hash": manifest.runner_hash,
        "test_suite_hash": manifest.test_suite_hash,
        "prompt_version": PROMPT_VERSION, "instruction_sha256": sha(INSTRUCTION.encode()),
        "schema_sha256": sha(canonical(schema)), "schema": schema,
        "authorization_draft": {
            "provider": "openai-responses", "model": None,
            "secret_variable_name": "ARC_APPROVED_MODEL_KEY", "budget_id": None,
            "request_limit": 3, "token_reservation_limit": 100000,
            "max_output_tokens_per_request": 2048, "task_request_limit": 3,
            "approved": False, "active_request_budget": 0, "active_token_budget": 0,
            "reservation_note": "local reservation units, not measured billable tokens or a currency cap",
        },
        "blocking_reasons": ["MODEL_AND_ACCOUNT_COMPATIBILITY_UNVERIFIED", "NO_EXPLICIT_POSITIVE_BUDGET_AUTHORIZATION"],
        "live_preconditions": [
            "explicit model/backend/secret-variable/budget authorization; never log the key",
            "verify selected model supports current schema, temperature and output settings",
            "fresh run and current independent Harness qualification",
            "freeze model/sampling/prompt/manifest/image and use one new budget ID",
        ],
        "required_evidence": [
            "actual baseline expected FAIL", "Task/Attempt/ToolCall/Candidate/Verification identities",
            "provider/model/response identity and usage, with missing values kept unknown",
            "each candidate patch and current trusted verdict", "final artifact bytes and SHA256",
            "budget reservations and unknown outcomes; no silent retry or Fake fallback",
        ],
        "completion_rules": {
            "delivered": "runtime SUCCEEDED + business DELIVERED + matching current PASS + real provider response evidence",
            "unsolved": "record actual failed attempts; no gold patch substitution or success cherry-picking",
            "scope": "one dev smoke is not a repair-rate benchmark; 72-task matrix needs separate authorization",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="case-01")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_plan(args.case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": plan["status"], "output": str(args.output), "network_requests": 0}))
