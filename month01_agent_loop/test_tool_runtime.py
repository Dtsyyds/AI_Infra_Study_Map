from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Barrier, Event
from time import monotonic, sleep

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


def wait_for_stats(
    runtime,
    predicate,
    *,
    timeout_seconds=1.0,
):
    deadline = monotonic() + timeout_seconds
    last_stats = runtime.stats_snapshot()

    while not predicate(last_stats):
        if monotonic() >= deadline:
            pytest.fail(f"等待 Runtime 指标状态超时，最后快照：{last_stats}")

        # 只是轮询退避，不是依靠固定等待保证正确性。
        sleep(0.001)
        last_stats = runtime.stats_snapshot()

    return last_stats


def test_stats_are_consistent_under_concurrent_load():
    worker_count = 4

    all_started = Barrier(worker_count + 1)
    release_tasks = Event()

    def blocking_operation():
        # 证明 4 个工具线程都已经开始运行。
        all_started.wait(timeout=5)

        # 阻止工具任务提前结束。
        if not release_tasks.wait(timeout=5):
            raise TimeoutError("等待测试释放工具任务超时")

        return "ok"

    with ThreadPoolExecutor(max_workers=worker_count) as tool_executor:
        runtime = ToolRuntime(
            executor=tool_executor,
            capacity=worker_count,
        )

        with ThreadPoolExecutor(max_workers=worker_count) as caller_executor:
            caller_futures = [
                caller_executor.submit(
                    runtime.run,
                    blocking_operation,
                    timeout_seconds=10,
                )
                for _ in range(worker_count)
            ]

            try:
                # 这里只能证明 4 个 operation 已启动。
                all_started.wait(timeout=5)

                # 继续等待调用线程完成提交后的指标更新。
                stats = wait_for_stats(
                    runtime,
                    lambda current: (
                        current.submitted_total == worker_count
                        and current.in_flight == worker_count
                    ),
                )

                assert stats.submitted_total == 4
                assert stats.in_flight == 4
                assert stats.rejected_total == 0

                # 4 个容量许可都被占用，
                # 第 5 个请求必须在 submit 前被拒绝。
                with pytest.raises(ToolRuntimeOverloadedError):
                    runtime.run(
                        lambda: "should-not-run",
                        timeout_seconds=0,
                    )

                # rejected_total 在当前主线程中同步更新，
                # 异常抛出后可以立即读取。
                stats = runtime.stats_snapshot()

                assert stats.submitted_total == 4
                assert stats.in_flight == 4
                assert stats.rejected_total == 1

            finally:
                # 无论前面的断言是否失败，
                # 都必须释放工具线程，避免线程池退出时挂死。
                release_tasks.set()

            # 等待 4 个调用线程得到工具返回值。
            results = [future.result(timeout=5) for future in caller_futures]

            assert results == ["ok"] * worker_count

            # 调用线程返回与 done callback 完成之间
            # 仍可能存在很短的并发窗口，因此继续等待 in_flight 清零。
            stats = wait_for_stats(
                runtime,
                lambda current: current.in_flight == 0,
            )

            assert stats.submitted_total == 4
            assert stats.in_flight == 0
            assert stats.rejected_total == 1
            assert stats.submit_failed_total == 0
            assert stats.caller_timeout_total == 0
