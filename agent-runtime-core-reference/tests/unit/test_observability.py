import logging

import pytest

from runtime.observability import (
    TelemetryHook,
    metrics_response,
    record_tool,
    set_persistent_metrics,
)


@pytest.mark.asyncio
async def test_trace_redacts_secrets_but_retains_correlation(caplog):
    with caplog.at_level(logging.INFO, logger="arc_reference.trace"):
        await TelemetryHook().emit(
            "on_tool",
            {
                "task_id": "task-correlation",
                "attempt_id": "attempt-correlation",
                "step_no": 2,
                "tool_call_id": "call-correlation",
                "candidate_no": 2,
                "verification_run_id": "verification-correlation",
                "text": "SIMULATED_SECRET_DO_NOT_LEAK",
                "password": "never-log-this",
            },
        )
    assert "task-correlation" in caplog.text
    assert "call-correlation" in caplog.text
    assert "verification-correlation" in caplog.text
    assert "SIMULATED_SECRET_DO_NOT_LEAK" not in caplog.text
    assert "never-log-this" not in caplog.text


def test_metrics_labels_are_bounded_and_exclude_correlation_ids():
    record_tool("document.fetch", "success")
    record_tool("repair.verify", "success")
    record_tool("tool-with-unbounded-user-input", "arbitrary-outcome")
    body, content_type = metrics_response()
    text = body.decode()
    assert "text/plain" in content_type
    assert 'tool="document.fetch"' in text
    assert 'tool="repair.verify"' in text
    assert 'outcome="other",tool="other"' in text
    assert "tool-with-unbounded-user-input" not in text
    assert "task_id=" not in text
    assert "attempt_id=" not in text
    assert "tool_call_id=" not in text
    assert "candidate_no=" not in text
    assert "verification_run_id=" not in text


def test_cross_process_metrics_are_refreshed_from_database_snapshot():
    set_persistent_metrics({
        "tasks": [{"status": "RUNNING", "count": 2}],
        "tools": [{"tool": "report.publish", "state": "UNKNOWN", "count": 1, "dispatch_count": 2}],
        "artifact_bytes": 123,
    })
    text = metrics_response()[0].decode()
    assert 'arc_reference_persisted_tasks{status="RUNNING"} 2.0' in text
    assert 'arc_reference_persisted_dispatches{state="UNKNOWN",tool="report.publish"} 2.0' in text
    assert "arc_reference_artifact_bytes 123.0" in text
    set_persistent_metrics({"tasks": [], "tools": [], "artifact_bytes": 0})
    assert 'arc_reference_persisted_tasks{status="RUNNING"}' not in metrics_response()[0].decode()
