import pytest

import agent as agent_module
from agent import LLMAgent
from execution_context import ExecutionContext


class FakeToolRuntime:
    """只用于验证对象是否被 Agent 向下传递。"""


class FakeClock:
    def __init__(self):
        self.current = 0.0

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def test_llm_agent_propagates_runtime_controls_to_executor(
    monkeypatch,
):
    captured = {}

    def fake_call_llm(prompt, *, timeout_seconds=None):
        return "Thought: 测试 Runtime 参数传播\nAction: Finish[任务完成]"

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
        captured["step_timeout_seconds"] = step_timeout_seconds

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
        ("overloaded", "工具运行时容量已满"),
    ],
)
def test_llm_agent_stops_after_terminal_runtime_result(
    monkeypatch,
    result_type,
    content,
):
    llm_call_count = 0
    execute_call_count = 0

    def fake_call_llm(prompt, *, timeout_seconds=None):
        nonlocal llm_call_count
        llm_call_count += 1
        return f'Thought: 测试 Runtime 结果\nAction: calculator(expression="{1 + 2 * 3}")'

    def fake_execute_action(
        action,
        *,
        context=None,
        tool_runtime=None,
        step_timeout_seconds=None,
    ):
        nonlocal execute_call_count
        execute_call_count += 1
        return {
            "type": result_type,
            "content": content,
            "success": False,
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


def test_llm_agent_skips_llm_when_context_is_cancelled(
    monkeypatch,
):
    context = ExecutionContext(timeout_seconds=10)
    context.cancel()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("失效任务不应调用下游组件")

    monkeypatch.setattr(
        agent_module,
        "call_llm",
        fail_if_called,
    )

    monkeypatch.setattr(
        agent_module,
        "build_prompt",
        fail_if_called,
    )

    monkeypatch.setattr(
        agent_module,
        "execute_action",
        fail_if_called,
    )

    agent = LLMAgent(max_steps=3)
    answer = agent.run("测试任务", context=context)

    assert answer == "任务已取消"
    snapshot = agent.last_trace.snapshot()

    assert snapshot["status"] == "cancelled"
    assert snapshot["final_answer"] == "任务已取消"
    assert snapshot["steps"] == []


def test_llm_agent_skips_llm_when_context_is_timed_out(
    monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        # 验证失效任务不应调用下游组件
        raise AssertionError("失效任务不应调用下游组件")

    monkeypatch.setattr(
        agent_module,
        "call_llm",
        fail_if_called,
    )

    monkeypatch.setattr(
        agent_module,
        "build_prompt",
        fail_if_called,
    )

    monkeypatch.setattr(
        agent_module,
        "execute_action",
        fail_if_called,
    )

    # context = ExecutionContext(timeout_seconds=10)
    # context.remaining_seconds() == 0    # 这一步只是一个 bool 判断
    clock = FakeClock()

    context = ExecutionContext(
        timeout_seconds=10,
        clock=clock,
    )

    clock.advance(10)

    assert context.remaining_seconds() == 0

    agent = LLMAgent(max_steps=3)
    answer = agent.run("测试任务", context=context)

    assert answer == "工具执行超时"
    snapshot = agent.last_trace.snapshot()

    assert snapshot["status"] == "timeout"
    assert snapshot["final_answer"] == "工具执行超时"
    assert snapshot["steps"] == []


def test_llm_agent_passes_effective_timeout_to_llm(
    monkeypatch,
):
    captured = {}

    def fake_call_llm(
        prompt,
        *,
        timeout_seconds=None,
    ):
        captured["prompt"] = prompt
        captured["timeout_seconds"] = timeout_seconds

        return "Thought: 测试 LLM timeout 传播\nAction: Finish[任务完成]"

    def fake_execute_action(
        action,
        *,
        context=None,
        tool_runtime=None,
        step_timeout_seconds=None,
    ):
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

    clock = FakeClock()
    context = ExecutionContext(
        timeout_seconds=10,
        clock=clock,
    )

    # 总预算只剩 2 秒。
    clock.advance(8)

    agent = LLMAgent(
        max_steps=1,
        llm_timeout_seconds=5,
    )

    answer = agent.run(
        "测试任务",
        context=context,
    )

    assert answer == "任务完成"

    # min(LLM 单次上限 5, 总预算剩余 2) == 2
    assert captured["timeout_seconds"] == 2
