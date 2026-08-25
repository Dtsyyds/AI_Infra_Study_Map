import pytest

import executor as executor_module
from execution_context import ExecutionContext, TaskCancelledError, TaskTimeoutError


class FakeClock:
    """不依赖真实 sleep 的测试时钟。"""

    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_expired_deadline_prevents_tool_execution(
    monkeypatch,
):
    """
    deadline 到期后，工具函数不能被调用。
    """
    clock = FakeClock()

    context = ExecutionContext(
        timeout_seconds=5,
        clock=clock,
    )

    # 模拟任务已经消耗了 5 秒。
    clock.advance(5)

    tool_called = False

    def fake_run_tool(tool_name, **kwargs):
        nonlocal tool_called
        tool_called = True
        return "TOOL_OK: unexpected"

    monkeypatch.setattr(
        executor_module,
        "run_tool",
        fake_run_tool,
    )

    result = executor_module.execute_action(
        'Action: calculator(expression="1 + 2")',
        context=context,
    )

    assert result == {
        "type": "timeout",
        "content": "任务已超过截止时间",
        "success": False,
    }

    # 这是本测试最重要的断言：
    # 超时不是工具执行后再修改结果，
    # 而是必须在副作用发生前阻止工具调用。
    assert tool_called is False


def test_context_without_deadline_remains_active():
    clock = FakeClock()
    context = ExecutionContext(clock=clock)

    clock.advance(10_000)

    assert context.remaining_seconds() is None
    assert context.is_timed_out() is False

    # 正常状态不返回特殊值，也不抛异常。
    assert context.check_active() is None


def test_check_active_raises_when_deadline_expires():
    clock = FakeClock()
    context = ExecutionContext(
        timeout_seconds=5,
        clock=clock,
    )

    clock.advance(5)

    with pytest.raises(TaskTimeoutError):
        context.check_active()


def test_cancelled_context_prevents_tool_execution(monkeypatch):
    context = ExecutionContext(timeout_seconds=5)
    context.cancel()

    tool_called = False

    def fake_run_tool(tool_name, **kwargs):
        nonlocal tool_called
        tool_called = True
        return "TOOL_OK: unexpected"

    monkeypatch.setattr(
        executor_module,
        "run_tool",
        fake_run_tool,
    )

    result = executor_module.execute_action(
        'Action: calculator(expression="1 + 2")',
        context=context,
    )

    assert result == {
        "type": "cancelled",
        "content": "任务已取消",
        "success": False,
    }
    assert tool_called is False


def test_cancellation_has_priority_over_timeout():
    clock = FakeClock()
    context = ExecutionContext(
        timeout_seconds=5,
        clock=clock,
    )

    context.cancel()
    clock.advance(5)

    with pytest.raises(TaskCancelledError):
        context.check_active()


def test_execute_action_still_works_without_context(monkeypatch):
    def fake_run_tool(tool_name, **kwargs):
        return "TOOL_OK: 3"

    monkeypatch.setattr(
        executor_module,
        "run_tool",
        fake_run_tool,
    )

    result = executor_module.execute_action(
        'Action: calculator(expression="1 + 2")',
    )

    assert result == {
        "type": "observation",
        "content": "TOOL_OK: 3",
        "success": True,
    }
