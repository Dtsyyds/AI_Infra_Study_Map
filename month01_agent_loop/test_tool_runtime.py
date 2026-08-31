from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest

from tool_runtime import (
    ToolExecutionTimeoutError,
    ToolRuntime,
    ToolRuntimeOverloadedError,
)


class TimedOutFuture:
    """模拟一个等待时发生超时的 Future。"""

    def __init__(self):
        self.received_timeout = None
        self.cancel_called = False
        self._done_callbacks = []

    def result(self, timeout=None):
        self.received_timeout = timeout
        raise FutureTimeoutError

    def cancel(self):
        self.cancel_called = True

        # False 表示任务已经开始，无法取消。
        return False

    def add_done_callback(self, callback):
        self._done_callbacks.append(callback)


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

    def add_done_callback(self, callback):
        callback(self)


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


class SequenceExecutor:
    """按照给定顺序返回 Future 或抛出异常。"""

    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.submit_count = 0

    def submit(self, _operation):
        self.submit_count += 1
        outcome = next(self._outcomes)

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


def test_running_future_keeps_capacity_after_caller_timeout():
    running_future = Future()
    assert running_future.set_running_or_notify_cancel()

    completed_future = Future()
    completed_future.set_result("second-ok")

    fake_executor = SequenceExecutor(
        [
            running_future,
            completed_future,
        ]
    )

    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    # 调用方超时，但 running_future 已经运行，无法取消。
    with pytest.raises(ToolExecutionTimeoutError):
        runtime.run(
            lambda: "first-ok",
            timeout_seconds=0,
        )

    # running_future 仍在运行，容量许可尚未释放。
    with pytest.raises(ToolRuntimeOverloadedError):
        runtime.run(
            lambda: "should-not-run",
            timeout_seconds=0,
        )

    # 被拒绝的任务没有进入 Executor。
    assert fake_executor.submit_count == 1

    # 模拟后台任务真正完成，done callback 应释放许可。
    running_future.set_result("late-result")

    result = runtime.run(
        lambda: "third-ok",
        timeout_seconds=0,
    )

    assert result == "second-ok"
    assert fake_executor.submit_count == 2


def test_submit_failure_releases_capacity():
    completed_future = Future()
    completed_future.set_result("ok")

    fake_executor = SequenceExecutor(
        [
            RuntimeError("submit failed"),
            completed_future,
        ]
    )

    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    # 第一次已经拿到许可，但 submit 本身失败。
    with pytest.raises(
        RuntimeError,
        match="submit failed",
    ):
        runtime.run(
            lambda: "first-ok",
            timeout_seconds=0,
        )

    # 如果 submit 失败时归还了许可，第二次就能成功。
    result = runtime.run(
        lambda: "second-ok",
        timeout_seconds=0,
    )

    assert fake_executor.submit_count == 2
    assert result == "ok"


@pytest.mark.parametrize("capacity", [0, -1])
def test_capacity_must_be_positive(capacity):
    fake_executor = SequenceExecutor([])

    with pytest.raises(
        ValueError,
        match="capacity",
    ):
        ToolRuntime(
            executor=fake_executor,
            capacity=capacity,
        )


def test_stats_keep_timed_out_running_future_in_flight():
    running_future = Future()
    assert running_future.set_running_or_notify_cancel()

    fake_executor = SequenceExecutor([running_future])
    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    with pytest.raises(ToolExecutionTimeoutError):
        runtime.run(
            lambda: "unused",
            timeout_seconds=0,
        )

    stats = runtime.stats_snapshot()

    assert stats.submitted_total == 1
    assert stats.caller_timeout_total == 1
    assert stats.in_flight == 1
    assert stats.rejected_total == 0
    assert stats.submit_failed_total == 0

    # Future 真正完成时，回调应将 in_flight 减一。
    running_future.set_result("late-result")

    stats = runtime.stats_snapshot()

    assert stats.submitted_total == 1
    assert stats.caller_timeout_total == 1
    assert stats.in_flight == 0

    # 必须在同一个锁中读取所有字段，否则可能得到“拼接快照


def test_stats_record_capacity_rejection():
    running_future = Future()
    assert running_future.set_running_or_notify_cancel()

    fake_executor = SequenceExecutor([running_future])
    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    with pytest.raises(ToolExecutionTimeoutError):
        runtime.run(
            lambda: "first",
            timeout_seconds=0,
        )

    with pytest.raises(ToolRuntimeOverloadedError):
        runtime.run(
            lambda: "rejected",
            timeout_seconds=0,
        )

    stats = runtime.stats_snapshot()

    assert fake_executor.submit_count == 1
    assert stats.submitted_total == 1
    assert stats.in_flight == 1
    assert stats.caller_timeout_total == 1
    assert stats.rejected_total == 1
    assert stats.submit_failed_total == 0

    running_future.set_result("late-result")

    assert runtime.stats_snapshot().in_flight == 0


def test_stats_record_submit_failure():
    fake_executor = SequenceExecutor([RuntimeError("submit failed")])
    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    with pytest.raises(
        RuntimeError,
        match="submit failed",
    ):
        runtime.run(
            lambda: "unused",
            timeout_seconds=1,
        )

    stats = runtime.stats_snapshot()

    assert fake_executor.submit_count == 1
    assert stats.submitted_total == 0
    assert stats.in_flight == 0
    assert stats.caller_timeout_total == 0
    assert stats.rejected_total == 0
    assert stats.submit_failed_total == 1


def test_stats_completed_future_is_not_in_flight():
    completed_future = Future()
    completed_future.set_result("ok")

    fake_executor = SequenceExecutor([completed_future])
    runtime = ToolRuntime(
        executor=fake_executor,
        capacity=1,
    )

    result = runtime.run(
        lambda: "unused",
        timeout_seconds=1,
    )

    stats = runtime.stats_snapshot()

    assert result == "ok"
    assert stats.submitted_total == 1
    assert stats.in_flight == 0
