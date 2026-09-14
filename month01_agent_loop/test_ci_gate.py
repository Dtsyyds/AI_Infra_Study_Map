"""
test_ci_gate.py

用测试证明退出码正确
"""
from eval_stability import determine_exit_code

import pytest

def test_ci_gate_returns_zero_when_all_runs_pass():
    summary = {"failed_runs": 0}
    assert determine_exit_code(summary) == 0

def test_ci_gate_returns_nonzero_when_any_runs_fail():
    summary = {"failed_runs": 1}
    assert determine_exit_code(summary) == 1

@pytest.mark.parametrize(
    "api_timeout_rate, expected_exit_code",
    [
        (0.04, 0),
        (0.05, 0),
        (0.06, 1),
    ],
)
def test_ci_gate_enforces_api_timeout_rate(
    api_timeout_rate,
    expected_exit_code,
):
    summary = {
        "failed_runs": 1,
        "api_timeout_rate": api_timeout_rate,
    }

    exit_code = determine_exit_code(
        summary,
        max_failed_runs=None,
        max_api_timeout_rate=0.05,
        )

    assert exit_code == expected_exit_code
