from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Event

import pytest

import executor as executor_module
from execution_context import ExecutionContext, TaskCancelledError, TaskTimeoutError
from tool_runtime import ToolExecutionTimeoutError, ToolRuntimeOverloadedError


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


@pytest.mark.parametrize(
    (
        "task_timeout",
        "elapsed",
        "step_timeout",
        "expected",
    ),
    [
        # 总任务和单步都无限制。
        (None, 0, None, None),
        # 只有单步限制。
        (None, 0, 5, 5),
        # 只有总任务限制。
        (10, 8, None, 2),
        # 总任务剩余时间更小。
        (10, 8, 5, 2),
        # 单步限制更小。
        (10, 2, 5, 5),
    ],
)
def test_effective_timeout_uses_smallest_available_budget(
    task_timeout,
    elapsed,
    step_timeout,
    expected,
):
    clock = FakeClock()

    context = ExecutionContext(
        timeout_seconds=task_timeout,
        clock=clock,
    )

    clock.advance(elapsed)

    result = context.effective_timeout_seconds(
        step_timeout_seconds=step_timeout,
    )

    assert result == expected


def test_effective_timeout_rejects_negative_step_timeout():
    context = ExecutionContext(timeout_seconds=10)

    with pytest.raises(
        ValueError,
        match="step_timeout_seconds",
    ):
        context.effective_timeout_seconds(
            step_timeout_seconds=-1,
        )


def test_future_timeout_does_not_stop_running_tool():
    """
    验证 Future 超时只停止调用方等待，
    不会强制终止正在运行的工作线程。
    """
    tool_started = Event()
    release_tool = Event()
    tool_finished = Event()

    def blocking_tool():
        tool_started.set()

        # 模拟工具调用被外部服务阻塞。
        release_tool.wait()

        tool_finished.set()
        return "TOOL_OK: finished"

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        future = executor.submit(blocking_tool)

        # 确保任务已经进入工作线程，
        # 避免测试的并发时序不确定。
        assert tool_started.wait(timeout=1)

        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.01)

        # 调用方已经超时，但工具还在运行。
        assert future.done() is False
        assert tool_finished.is_set() is False

        # 已经开始运行的线程任务通常无法取消。
        assert future.cancel() is False

    finally:
        # 无论断言是否失败，都必须释放工作线程。
        release_tool.set()
        executor.shutdown(wait=True)

    assert tool_finished.is_set() is True
    assert future.result() == "TOOL_OK: finished"


class RecordingToolRuntime:
    """记录 executor 传入的有效 timeout。"""

    def __init__(self):
        self.received_timeout = None

    def run(
        self,
        operation,
        *,
        timeout_seconds,
    ):
        self.received_timeout = timeout_seconds
        return operation()


class TimingOutToolRuntime:
    """模拟 ToolRuntime 等待工具时发生超时。"""

    def __init__(self):
        self.received_timeout = None

    def run(
        self,
        operation,
        *,
        timeout_seconds,
    ):
        self.received_timeout = timeout_seconds
        raise ToolExecutionTimeoutError("工具执行超时")


def test_execute_action_passes_effective_timeout_to_tool_runtime(
    monkeypatch,
):
    tool_call_count = 0

    def fake_run_tool(tool_name, **kwargs):
        nonlocal tool_call_count
        tool_call_count += 1
        return "TOOL_OK: 3"

    monkeypatch.setattr(
        executor_module,
        "run_tool",
        fake_run_tool,
    )

    clock = FakeClock()

    context = ExecutionContext(
        timeout_seconds=10,
        clock=clock,
    )

    # Agent 总预算只剩 2 秒。
    clock.advance(8)

    runtime = RecordingToolRuntime()

    # monkeypatch.setattr(
    #     executor_module,
    #     "run_tool",
    #     lambda tool_name, **kwargs: "TOOL_OK: 3",
    # )

    result = executor_module.execute_action(
        'Action: calculator(expression="1 + 2")',
        context=context,
        tool_runtime=runtime,
        step_timeout_seconds=5,
    )

    # min(单步上限 5, 总任务剩余 2) == 2
    assert runtime.received_timeout == 2

    assert result == {
        "type": "observation",
        "content": "TOOL_OK: 3",
        "success": True,
    }

    assert tool_call_count == 1


def test_execute_action_maps_tool_runtime_timeout_to_result():
    runtime = TimingOutToolRuntime()

    result = executor_module.execute_action(
        'Action: calculator(expression="1 + 2")',
        tool_runtime=runtime,
        step_timeout_seconds=2,
    )

    assert runtime.received_timeout == 2

    assert result == {
        "type": "timeout",
        "content": "工具执行超时",
        "success": False,
    }


def test_execute_action_finish_does_not_require_tool_fields():
    result = executor_module.execute_action(
        "Action: Finish[任务完成]",
    )

    assert result == {
        "type": "finish",
        "content": "任务完成",
    }


def test_execute_action_parse_error_does_not_require_tool_fields():
    result = executor_module.execute_action(
        "这不是合法的 Action",
    )

    assert result["type"] == "error"
    assert "content" in result


class OverloadedToolRuntime:
    def run(
        self,
        operation,
        *,
        timeout_seconds,
    ):
        raise ToolRuntimeOverloadedError(
            "工具运行时容量已满",
        )


def test_execute_action_maps_runtime_overload_to_result(
    monkeypatch,
):
    run_tool_count = 0

    def fake_run_tool(
        *args,
        **__kwargs,
    ):
        nonlocal run_tool_count
        run_tool_count += 1
        return "TOOL_OK: should-not-run"

    monkeypatch.setattr(
        executor_module,
        "run_tool",
        fake_run_tool,
    )

    result = executor_module.execute_action(
        action_text="Action: calculator(expression='1 + 2 * 4')",
        context=None,
        tool_runtime=OverloadedToolRuntime(),
        step_timeout_seconds=0,
    )

    assert result == {
        "type": "overloaded",
        "content": "工具运行时容量已满",
        "success": False,
    }
    assert run_tool_count == 0
