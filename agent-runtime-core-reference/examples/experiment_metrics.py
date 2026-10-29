"""Pure, recomputable experiment accounting; never execute candidates or impute missing data."""

import math
from collections import Counter, defaultdict
from datetime import datetime

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "NEEDS_REVIEW"}


def timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def interval(end, start):
    if end is None or start is None:
        return None
    value = (timestamp(end) - timestamp(start)).total_seconds()
    if value < 0:
        raise ValueError("negative observed duration")
    return value


def task_timings(diag):
    task = diag["task"]
    attempts = sorted(diag.get("attempts", []), key=lambda row: row["started_at"])
    finish = task["updated_at"] if task["status"] in TERMINAL else None
    first = attempts[0]["started_at"] if attempts else None
    return {
        "queue_seconds": interval(first or finish, task["created_at"]),
        "execution_seconds": interval(finish, first),
        "total_seconds": interval(finish, task["created_at"]),
        "attempt_reclaim_gaps_seconds": [
            interval(b["started_at"], a.get("finished_at"))
            for a, b in zip(attempts, attempts[1:], strict=False)
        ],
        "timing_note": "execution is first claim to terminal including recovery; reclaim gaps are recorded attempt finish to next start, not crash outage; unclaimed execution is null",
    }


def distribution(values):
    values = sorted(v for v in values if v is not None)
    return {
        "measured_n": len(values),
        "p50": values[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "max": max(values) if values else None,
        "small_sample_p95_unstable": 0 < len(values) < 100,
    }


def model_accounting(diag):
    calls = diag.get("model_calls", [])
    candidates = diag.get("repair_candidates", [])
    errors = []
    seen = set()
    actual_models = set()
    for call in calls:
        if call["call_id"] in seen:
            errors.append("DUPLICATE_MODEL_CALL_ID")
        seen.add(call["call_id"])
        meta = call.get("result") or {}
        if meta.get("provider") == "fake":
            errors.append("FAKE_IN_REAL_GROUP")
        if meta.get("provider") not in (None, "openai-responses"):
            errors.append("UNEXPECTED_PROVIDER")
        if meta.get("model"):
            actual_models.add(meta["model"])
    for candidate in candidates:
        # Candidate generation metadata is also in the checkpoint observation.
        if candidate.get("generation", {}).get("provider") == "fake":
            errors.append("FAKE_IN_REAL_GROUP")
    for step in diag.get("steps", []):
        observation = step.get("result", {})
        if observation.get("generation", {}).get("provider") == "fake":
            errors.append("FAKE_IN_REAL_GROUP")
    task = diag["task"]
    if task.get("request", {}).get("planner_mode") != "real":
        errors.append("NON_REAL_TASK")
    result = task.get("result") or {}
    delivered = task["status"] == "SUCCEEDED" and result.get("business_outcome") == "DELIVERED"
    checked = [
        r
        for r in diag.get("repair_verifications", [])
        if r.get("result") and r["result"].get("verdict", {}).get("status") == "PASS"
    ]
    matching_pass = any(
        r["identity"]["candidate_no"] == result.get("candidate_no")
        and r["identity"]["candidate_hash"] == result.get("candidate_hash")
        for r in checked
    )
    if delivered and (not matching_pass or not calls or not actual_models):
        errors.append("DELIVERY_WITHOUT_REAL_CALL_AND_CURRENT_PASS")

    def usage(field):
        values = [(c.get("result") or {}).get(field) for c in calls]
        known = [v for v in values if type(v) is int and v >= 0]
        return {"known_sum": sum(known), "missing_calls": len(values) - len(known)}

    return {
        "calls": sum(
            bool((c.get("result") or {}).get("request_id"))
            and (c.get("result") or {}).get("provider") == "openai-responses"
            for c in calls
        ),
        "reserved_attempts": len(calls),
        "unconfirmed_reserved_attempts": sum(
            not ((c.get("result") or {}).get("request_id")) for c in calls
        ),
        "reserved_tokens": sum(c["reserved_tokens"] for c in calls),
        "input_tokens": usage("input_tokens"),
        "output_tokens": usage("output_tokens"),
        "actual_models": sorted(actual_models),
        "candidate_count": len(candidates),
        "delivered": delivered and not errors,
        "errors": sorted(set(errors)),
        "price": None,
    }


def summarize_experiment(rows, *, kind, planned):
    if kind not in {"model", "load"}:
        raise ValueError("unknown experiment kind")
    if len(rows) > planned:
        raise ValueError("observations exceed preregistered denominator")
    groups = defaultdict(list)
    seen = set()
    slots = set()
    for row in rows:
        identity = row["submission_id"]
        if identity in seen:
            raise ValueError("duplicate submission cannot inflate sample count")
        seen.add(identity)
        if kind == "model" and row.get("model_accounting", {}).get("errors"):
            raise ValueError("invalid real model evidence")
        group = row["group"] if kind == "model" else str(row["concurrency"])
        slot = (
            group,
            row["round"],
            row.get("case_id") if kind == "model" else row.get("index", identity),
        )
        if slot in slots:
            raise ValueError("duplicate experimental slot")
        slots.add(slot)
        groups[(group, row["round"])].append(row)
    output = []
    for (group, repeat), records in sorted(groups.items()):
        counts = Counter(r["status"] for r in records)
        buckets = []
        for status in sorted(counts):
            selected = [r for r in records if r["status"] == status]
            buckets.append(
                {
                    "status": status,
                    "n": len(selected),
                    "N_observed_round": len(records),
                    "durations": {
                        name: distribution([r.get("timings", {}).get(name) for r in selected])
                        for name in ("queue_seconds", "execution_seconds", "total_seconds")
                    },
                }
            )
        output.append(
            {
                "group": group,
                "round": repeat,
                "observed": len(records),
                "planned_in_round": 8 if kind == "model" else 30,
                "status_counts": dict(counts),
                "buckets": buckets,
                "model_cost": {
                    "candidate_total": sum(r.get("model_accounting", {}).get("candidate_count", 0) for r in records),
                    "reserved_attempts": sum(r.get("model_accounting", {}).get("reserved_attempts", 0) for r in records),
                    "reserved_tokens": sum(r.get("model_accounting", {}).get("reserved_tokens", 0) for r in records),
                    "usage": {field: {key: sum(r.get("model_accounting", {}).get(field, {}).get(key, 0) for r in records)
                                      for key in ("known_sum", "missing_calls")}
                              for field in ("input_tokens", "output_tokens")},
                    "price": None,
                } if kind == "model" else None,
                "delivered": sum(
                    r.get("model_accounting", {}).get("delivered", False) for r in records
                )
                if kind == "model"
                else None,
                "model_calls": sum(r.get("model_accounting", {}).get("calls", 0) for r in records)
                if kind == "model"
                else None,
            }
        )
    return {
        "kind": kind,
        "planned": planned,
        "observed": len(rows),
        "missing": planned - len(rows),
        "status": "COMPLETE_RECORDS" if len(rows) == planned else "INCOMPLETE",
        "groups": output,
        "warning": "HTTP202 is not success; rejection latency never pooled with terminal successes; no speedup claim; missing usage is not zero",
    }


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["model", "load"])
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.input.read_text().splitlines() if line]
    summary = summarize_experiment(records, kind=args.kind, planned=72 if args.kind == "model" else 270)
    args.output.write_text(json.dumps(summary, indent=2))
    print(json.dumps({"kind": args.kind, "observed": summary["observed"], "missing": summary["missing"]}))
