import pytest

import agent as agent_module
from agent import LLMAgent
from execution_context import ExecutionContext


class FakeToolRuntime:
    """只用于验证对象是否被 Agent 向下传递。"""


def test_llm_agent_propagates_runtime_controls_to_executor(
    monkeypatch,
):
    captured = {}

    def fake_call_llm(prompt):
        return (
            "Thought: 测试 Runtime 参数传播\n"
            "Action: Finish[任务完成]"
        )

    def fake_execute_action(
        action,
        *,
        context=None,
        tool_runtime=None,
        step_timeout_seconds=None,
    ):
        captured["action"] = action
        captured["context"] = context
        captured["tool_runtime"] = tool_runtime
        captured["step_timeout_seconds"] = (
            step_timeout_seconds
        )

        return {
            "type": "finish",
            "content": "任务完成",
        }

    monkeypatch.setattr(
        agent_module,
        "call_llm",
        fake_call_llm,
    )
    monkeypatch.setattr(
        agent_module,
        "execute_action",
        fake_execute_action,
    )

    context = ExecutionContext(timeout_seconds=10)
    tool_runtime = FakeToolRuntime()

    agent = LLMAgent(
        max_steps=1,
        tool_runtime=tool_runtime,
        step_timeout_seconds=2,
    )

    answer = agent.run(
        "测试任务",
        context=context,
    )

    assert answer == "任务完成"
    assert captured["context"] is context
    assert captured["tool_runtime"] is tool_runtime
    assert captured["step_timeout_seconds"] == 2

@pytest.mark.parametrize(
    ("result_type", "content"),
    [
        ("timeout", "工具执行超时"),
        ("cancelled", "任务已取消"),
    ],
)
def test_llm_agent_stops_after_terminal_runtime_result(
    monkeypatch,
    result_type,
    content,
):
    llm_call_count = 0
    execute_call_count = 0

    def fake_call_llm(prompt):
        nonlocal llm_call_count
        llm_call_count += 1
        return (
            "Thought: 测试 Runtime 结果\n"
            f'Action: calculator(expression="{1 + 2 * 3}")'
        )

    def fake_execute_action(
            action,
            *,
            context=None,
            tool_runtime=None,
            step_timeout_seconds=None,
    ):
        nonlocal execute_call_count
        execute_call_count += 1
        return (
            {
                "type": result_type,
                "content": content,
                "success": False,
            }
        )

    monkeypatch.setattr(
        agent_module,
        "call_llm",
        fake_call_llm,
    )
    monkeypatch.setattr(
        agent_module,
        "execute_action",
        fake_execute_action,
    )

    agent = LLMAgent(max_steps=3)

    answer = agent.run("测试任务")

    assert llm_call_count == 1
    assert execute_call_count == 1
    assert answer == content

    snapshot = agent.last_trace.snapshot()

    assert snapshot["status"] == result_type
    assert snapshot["final_answer"] == content
    assert len(snapshot["steps"]) == 1

    assert agent.memory.get_message()[-1] == {
        "role": "assistant",
        "content": f"Final Answer: {content}",
    }