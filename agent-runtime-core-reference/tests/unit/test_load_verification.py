import hashlib
import json

import pytest

from examples.experiment_metrics import summarize_experiment, task_timings
from examples.verify_load import verify, verify_records


@pytest.fixture
def evidence():
    plan = {"concurrency": [1], "rounds": 1, "submissions_per_round": 1, "total": 1}
    request = {"session_id": "s1", "document_id": "DOC-002", "budget_ms": 120000}
    slot = {"index": 0, "concurrency": 1, "round": 1}
    artifact = {"artifact_id": "a1", "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}
    row = {**slot, "submission_id": "s1", "task_id": "t1", "http_status": 202,
           "status": "SUCCEEDED", "runtime_status": "SUCCEEDED", "errors": [],
           "diagnostics": {"task": {"task_id": "t1", "session_id": "s1", "request": request,
                                    "created_at": "2026-10-26T00:00:00+00:00", "updated_at": "2026-10-26T00:00:01+00:00",
                                    "status": "SUCCEEDED"}, "artifacts": [artifact], "model_calls": []},
           "artifacts": [{**artifact, "file": "old/location/artifact-a1.json"}]}
    row["timings"] = task_timings(row["diagnostics"])
    events = [{"submission_id": "s1", "event": "SUBMISSION_INTENT", "model": False,
               "request": dict(request), "slot": slot},
              {"submission_id": "s1", "event": "ACCEPTED", "task_id": "t1"},
              {"submission_id": "s1", "event": "OBSERVATION_FINISHED", "task_id": "t1",
               "status": "SUCCEEDED", "record_file": "load_raw.jsonl"}]
    return plan, [row], events


def test_complete_success_and_rejection_are_separate(evidence):
    plan, rows, events = evidence
    assert verify_records(*evidence)["outcomes"] == {"SUCCEEDED": 1}
    rows[0].update(http_status=429, status="REJECTED", task_id=None)
    events.pop(1)
    events[-1].update(status="REJECTED", task_id=None)
    result = verify_records(plan, rows, events)
    assert result["accepted_tasks"] == 0 and result["outcomes"] == {"REJECTED": 1}


@pytest.mark.parametrize("broken,expected", [
    ("unresolved", "UNRESOLVED_JOURNAL_IDENTITY"),
    ("missing_accept", "JOURNAL_SEQUENCE_MISMATCH"),
    ("finish", "JOURNAL_RESULT_MISMATCH"),
    ("slot", "SLOT_MISMATCH"),
    ("request", "DIAGNOSTIC_REQUEST_MISMATCH"),
    ("identity", "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("nonterminal", "NONTERMINAL_OR_MISMATCHED_RESULT"),
    ("model", "MODEL_IN_DETERMINISTIC_LOAD"),
    ("artifact", "ARTIFACT_INVENTORY_MISMATCH"),
    ("missing_result", "INCOMPLETE_RECORDS"),
    ("timing", "TIMING_MISMATCH"),
])
def test_inconsistent_load_evidence_cannot_pass(evidence, broken, expected):
    plan, rows, events = evidence
    if broken == "unresolved":
        events.append({"submission_id": "lost", "event": "SUBMISSION_INTENT"})
    elif broken == "missing_accept":
        events.pop(1)
    elif broken == "finish":
        events[-1]["task_id"] = "wrong"
    elif broken == "slot":
        rows[0]["index"] = 1
    elif broken == "request":
        events[0]["request"]["document_id"] = "DOC-001"
    elif broken == "identity":
        rows[0]["diagnostics"]["task"]["session_id"] = "wrong"
    elif broken == "nonterminal":
        rows[0]["diagnostics"]["task"]["status"] = "RUNNING"
    elif broken == "model":
        rows[0]["diagnostics"]["model_calls"] = [{"call_id": "unexpected"}]
    elif broken == "artifact":
        rows[0]["artifacts"] = []
    elif broken == "missing_result":
        rows.clear()
    elif broken == "timing":
        rows[0]["timings"]["total_seconds"] = 0
    with pytest.raises(ValueError, match=expected):
        verify_records(plan, rows, events)


def write_evidence(directory, plan, rows, events):
    (directory / "load_config.json").write_text(json.dumps(plan))
    (directory / "load_raw.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (directory / "load_raw_events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (directory / "load_summary.json").write_text(json.dumps(summarize_experiment(rows, kind="load", planned=plan["total"])))
    (directory / "worker-t1.log").write_text("worker completed")
    (directory / "artifact-a1.json").write_bytes(b"{}")


def test_relocated_directory_rechecks_bytes_and_summary(tmp_path, evidence):
    write_evidence(tmp_path, *evidence)
    assert verify(tmp_path)["integrity_status"] == "PASS"
    (tmp_path / "artifact-a1.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="ARTIFACT_HASH_MISMATCH"):
        verify(tmp_path)
    (tmp_path / "artifact-a1.json").write_bytes(b"{}")
    (tmp_path / "load_summary.json").write_text('{}')
    with pytest.raises(ValueError, match="SUMMARY_MISMATCH"):
        verify(tmp_path)


def test_journal_cannot_count_duplicate_task_twice(evidence):
    import copy

    plan, rows, events = evidence
    plan.update(submissions_per_round=2, total=2)
    second = copy.deepcopy(rows[0])
    second.update(submission_id="s2", index=1)
    extra = copy.deepcopy(events)
    for event in extra:
        event["submission_id"] = "s2"
    extra[0]["request"]["session_id"] = "s2"
    extra[0]["slot"]["index"] = 1
    with pytest.raises(ValueError, match="DUPLICATE_TASK"):
        verify_records(plan, rows + [second], events + extra)
