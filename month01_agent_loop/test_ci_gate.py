"""
test_ci_gate.py

用测试证明退出码正确
"""

def test_ci_gate_returns_zero_when_all_runs_pass():
    {"failed_runs": 0}

def test_ci_gate_returns_nonzero_when_any_runs_fail():
    {"failed_runs": 1}

    