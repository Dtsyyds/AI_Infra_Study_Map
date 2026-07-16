"""
test_error_paths.py

测试抛出异常情况下 trace 能否正确捕获，以及 Agent 的处理方案
"""
from eval import run_eval_case
from trace import AgentTrace

class ThrowingAgent:
    """
    用于测试抛出异常的 Agent
    不访问真实的 LLM, 每次调用 run 都稳定抛出异常
    """
    last_trace = None

    def run(self, user_input: str):
        raise RuntimeError("mock agent failure")
    
def test_run_eval_case_handles_agent_exception():
    """
    测试 run_eval_case 在 Agent 抛出异常时能否正确捕获并处理。
    """
    agent = ThrowingAgent()
    case = {
        "id": "agent_exception",
        "input": "触发测试异常"
    }
    result = run_eval_case(agent, case)
    # Assert：失败被转换成结构化数据
    assert result["answer"] == ""
    assert result["passed"] is False
    assert result["answer_passed"] is False
    assert result["trace_passed"] is False
    assert result["trace_summary"] == {}

    assert result["error"] == "mock agent failure"

    assert result["check_reasons"] == [
        "执行出错: mock agent failure"
    ]

    assert isinstance(
        result["duration_seconds"],
        float,
    )
    assert result["duration_seconds"] >= 0

def test_trace_fail_uses_consistent_schema(tmp_path):
    # Arrange: 日志写入 pytest 提供的临时目录
    trace = AgentTrace(tmp_path)
    trace.set_user_input("测试 Trace 失败处理")

    trace.fail("mock trace failure")

    data = trace.snapshot()

     # Assert：失败 Trace 字段完整且命名一致
    assert data["status"] == "failed"
    assert data["error"] == "mock trace failure"
    assert data["end_time"] is not None

    assert isinstance(
        data["duration_seconds"],
        float,
    )
    assert data["duration_seconds"] >= 0

    # 旧错误字段不能继续存在
    assert "duration" not in data

    # 验证日志确实写入了临时目录
    log_file = tmp_path / "agent_trace.jsonl"
    assert log_file.exists()

