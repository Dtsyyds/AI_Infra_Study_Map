import pytest

import eval_stability as stability_module


class FakeAgent:
    def __init__(self, max_steps):
        self.max_steps = max_steps


@pytest.mark.parametrize(
    (
        "case_id",
        "category",
        "trace_status",
        "expected_failure_type",
        "tool_calls",
        "expected_tool_path",
    ),
    [
        (
            "repeated_action_case",
            "tool_use",
            "stopped",
            "repeated_action",
            ["calculator"],
            "calculator",
        ),
        (
            "llm_error_case",
            "llm",
            "llm_error",
            "api_error",
            [],
            "(no tools)",
        ),
        (
            "llm_timeout_case",
            "llm",
            "llm_timeout",
            "api_timeout",
            [],
            "(no tools)",
        ),
    ],
)
def test_run_stability_eval_propagates_trace_status(
    monkeypatch,
    case_id,
    category,
    trace_status,
    expected_failure_type,
    tool_calls,
    expected_tool_path,
):
    case = {
        "id": case_id,
        "input": f"模拟 {trace_status}",
        "category": category,
    }

    def fake_run_eval_case(
        agent,
        received_case,
    ):
        assert agent.max_steps == 3
        assert received_case is case

        return {
            "id": case["id"],
            "passed": False,
            "answer_passed": False,
            "trace_passed": False,
            "check_reasons": [
                (
                    "期望的 Trace 状态是 success，"
                    f"实际为 {trace_status}"
                ),
            ],
            "trace_summary": {
                "trace_status": trace_status,
                "tool_calls": tool_calls,
            },
            "duration_seconds": 0.01,
        }

    monkeypatch.setattr(
        stability_module,
        "LLMAgent",
        FakeAgent,
    )
    monkeypatch.setattr(
        stability_module,
        "run_eval_case",
        fake_run_eval_case,
    )

    results = stability_module.run_stability_eval(
        cases=[case],
        runs=1,
        max_steps=3,
    )

    assert len(results) == 1

    result = results[0]

    assert result["case_id"] == case["id"]
    assert result["tool_path"] == expected_tool_path

    assert result["failure_types"] == [
        expected_failure_type,
        "answer_mismatch",
        "trajectory_mismatch",
    ]

    assert (
        result["primary_failure_type"]
        == expected_failure_type
    )

def test_build_case_summary_separates_primary_api_failures():
    case = {
        "id": "llm_reliability_case",
    }

    case_results = [
        {
            "passed": False,
            "duration_seconds": 1.0,
            "trace_summary": {
                "tool_calls": [],
            },
            "failure_types": [
                "api_timeout",
                "answer_mismatch",
                "trajectory_mismatch",
            ],
            "primary_failure_type": "api_timeout",
        },
        {
            "passed": False,
            "duration_seconds": 2.0,
            "trace_summary": {
                "tool_calls": [],
            },
            "failure_types": [
                "api_timeout",
                "answer_mismatch",
                "trajectory_mismatch",
            ],
            "primary_failure_type": "api_timeout",
        },
        {
            "passed": False,
            "duration_seconds": 3.0,
            "trace_summary": {
                "tool_calls": [],
            },
            "failure_types": [
                "api_error",
                "answer_mismatch",
                "trajectory_mismatch",
            ],
            "primary_failure_type": "api_error",
        },
    ]

    summary = stability_module.build_case_summary(
        case,
        case_results,
    )

    assert summary["total_runs"] == 3
    assert summary["passed_runs"] == 0
    assert summary["failed_runs"] == 3
    assert summary["pass_rate"] == 0.0
    assert summary["average_duration_seconds"] == 2.0

    assert summary["tool_paths"] == {
        "(no tools)": 3,
    }

    assert summary["primary_failure_types"] == {
        "api_timeout": 2,
        "api_error": 1,
    }

def test_build_case_summary_reports_api_timeout_rate():
    case = {
        "id": "llm_timeout_rate_case",
    }

    case_results = [
        {
            "passed": False,
            "duration_seconds": 1.0,
            "trace_summary": {
                "tool_calls": [],
            },
            "primary_failure_type": "api_timeout",
        },
        {
            "passed": False,
            "duration_seconds": 2.0,
            "trace_summary": {
                "tool_calls": [],
            },
            "primary_failure_type": "api_timeout",
        },
        {
            "passed": False,
            "duration_seconds": 1.5,
            "trace_summary": {
                "tool_calls": [],
            },
            "primary_failure_type": "api_error",
        },
        {
            "passed": True,
            "duration_seconds": 0.5,
            "trace_summary": {
                "tool_calls": [],
            },
            "primary_failure_type": None,
        },
    ]

    summary = stability_module.build_case_summary(
        case,
        case_results,
    )

    assert summary["total_runs"] == 4
    assert summary["failed_runs"] == 3

    assert summary["api_timeout_runs"] == 2
    assert summary["api_timeout_rate"] == 0.5

    assert summary["passed_runs"] == 1
    assert summary["pass_rate"] == 0.25

    assert summary["primary_failure_types"] == {
        "api_timeout": 2,
        "api_error": 1,
    }

    assert summary["api_timeout_runs"] == 2
    assert summary["api_timeout_rate"] == 0.5