"""
test_ci_gate.py

用测试证明退出码正确
"""
from eval_stability import determine_exit_code


def test_ci_gate_returns_zero_when_all_runs_pass():
    summary = {"failed_runs": 0}
    assert determine_exit_code(summary) == 0

def test_ci_gate_returns_nonzero_when_any_runs_fail():
    summary = {"failed_runs": 1}
    assert determine_exit_code(summary) == 1
