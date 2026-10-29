import copy

import pytest

from examples.experiment_metrics import model_accounting, summarize_experiment, task_timings


def diag(status="SUCCEEDED", claimed=True):
    return {
        "task": {
            "status": status,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:10+00:00",
            "request": {"planner_mode": "real"},
            "result": None,
        },
        "attempts": [
            {"started_at": "2026-01-01T00:00:03+00:00", "finished_at": "2026-01-01T00:00:10+00:00"}
        ]
        if claimed
        else [],
    }


def test_queue_execution_and_unclaimed_timeout_are_distinct():
    result = task_timings(diag())
    assert (result["queue_seconds"], result["execution_seconds"], result["total_seconds"]) == (
        3,
        7,
        10,
    )
    timeout = task_timings(diag("TIMED_OUT", claimed=False))
    assert timeout["queue_seconds"] == 10 and timeout["execution_seconds"] is None
    running = task_timings(diag("RUNNING"))
    assert running["execution_seconds"] is None and running["total_seconds"] is None


def test_rejections_do_not_improve_success_latency_or_disappear_from_denominator():
    rows = [
        {
            "submission_id": "one",
            "concurrency": 5,
            "round": 1,
            "status": "SUCCEEDED",
            "timings": task_timings(diag()),
        },
        {"submission_id": "two", "concurrency": 5, "round": 1, "status": "REJECTED", "timings": {}},
    ]
    output = summarize_experiment(rows, kind="load", planned=270)
    assert output["observed"] == 2 and output["missing"] == 268
    group = output["groups"][0]
    assert group["status_counts"] == {"SUCCEEDED": 1, "REJECTED": 1}
    assert (
        next(b for b in group["buckets"] if b["status"] == "SUCCEEDED")["durations"][
            "execution_seconds"
        ]["p50"]
        == 7
    )
    with pytest.raises(ValueError, match="duplicate"):
        summarize_experiment(rows + rows, kind="load", planned=270)


def test_model_fake_replay_and_missing_usage_are_not_real_success():
    d = diag()
    d["model_calls"] = [{"call_id": "call", "reserved_tokens": 5000, "result": None}]
    result = model_accounting(d)
    assert result["input_tokens"] == {"known_sum": 0, "missing_calls": 1}
    assert not result["delivered"]
    d["steps"] = [{"result": {"generation": {"provider": "fake"}}}]
    assert "FAKE_IN_REAL_GROUP" in model_accounting(d)["errors"]
    bad = copy.deepcopy(d)
    bad["task"]["result"] = {
        "business_outcome": "DELIVERED",
        "candidate_no": 1,
        "candidate_hash": "a",
    }
    assert "DELIVERY_WITHOUT_REAL_CALL_AND_CURRENT_PASS" in model_accounting(bad)["errors"]
    with pytest.raises(ValueError, match="invalid real model"):
        summarize_experiment(
            [{"submission_id": "s", "model_accounting": model_accounting(bad)}],
            kind="model",
            planned=72,
        )


def test_per_round_model_cost_preserves_extra_candidates_and_unknown_usage():
    d = diag("FAILED")
    d["model_calls"] = [{"call_id": "one", "reserved_tokens": 5000, "result": None}]
    d["repair_candidates"] = [{"candidate_no": 1}, {"candidate_no": 2}]
    accounting = model_accounting(d)
    row = {"submission_id": "s", "group": "C", "round": 1, "case_id": "case-05", "status": "FAILED", "model_accounting": accounting}
    result = summarize_experiment([row], kind="model", planned=72)["groups"][0]
    assert result["model_calls"] == 0
    assert result["model_cost"]["reserved_attempts"] == 1
    assert result["model_cost"]["candidate_total"] == 2
    assert result["model_cost"]["usage"]["input_tokens"] == {"known_sum": 0, "missing_calls": 1}
    assert result["model_cost"]["price"] is None
