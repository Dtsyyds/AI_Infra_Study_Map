from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)

import pytest

from tool_runtime import (
    ToolExecutionTimeoutError,
    ToolRuntime,
)


class TimedOutFuture:
    """模拟一个等待时发生超时的 Future。"""

    def __init__(self):
        self.received_timeout = None
        self.cancel_called = False

    def result(self, timeout=None):
        self.received_timeout = timeout
        raise FutureTimeoutError

    def cancel(self):
        self.cancel_called = True

        # False 表示任务已经开始，无法取消。
        return False


class RecordingExecutor:
    """记录 submit 行为的测试 Executor。"""

    def __init__(self, future):
        self.future = future
        self.submitted_operation = None

    def submit(self, operation):
        self.submitted_operation = operation
        return self.future


class CompletedFuture:
    """模拟已经成功完成的 Future。"""

    def __init__(self, value):
        self.value = value
        self.received_timeout = None
        self.cancel_called = False

    def result(self, timeout=None):
        self.received_timeout = timeout
        return self.value

    def cancel(self):
        self.cancel_called = True
        return False


def test_tool_runtime_maps_future_timeout_to_domain_error():
    future = TimedOutFuture()
    executor = RecordingExecutor(future)
    runtime = ToolRuntime(executor)

    with pytest.raises(
        ToolExecutionTimeoutError,
        match="工具执行超时",
    ):
        runtime.run(
            lambda: "TOOL_OK: unused",
            timeout_seconds=2,
        )

    assert executor.submitted_operation is not None
    assert future.received_timeout == 2
    assert future.cancel_called is True


def test_tool_runtime_rejects_negative_timeout_before_submit():
    future = TimedOutFuture()
    executor = RecordingExecutor(future)
    runtime = ToolRuntime(executor)

    with pytest.raises(
        ValueError,
        match="timeout_seconds",
    ):
        runtime.run(
            lambda: "TOOL_OK: unused",
            timeout_seconds=-1,
        )

    # 非法参数必须在产生调度行为之前拒绝。
    assert executor.submitted_operation is None


def test_tool_runtime_returns_completed_result():
    future = CompletedFuture("TOOL_OK: completed")
    executor = RecordingExecutor(future)
    runtime = ToolRuntime(executor)

    result = runtime.run(
        lambda: "TOOL_OK: unused",
        timeout_seconds=2,
    )

    assert result == "TOOL_OK: completed"
    assert future.received_timeout == 2
    assert future.cancel_called is False
