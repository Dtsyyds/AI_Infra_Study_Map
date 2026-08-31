from collections.abc import Callable
from concurrent.futures import (
    Executor,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)

# BoundedSemaphore 内部维护一个计数器和一个条件变量。
from threading import BoundedSemaphore
from typing import TypeVar

T = TypeVar("T")


class ToolExecutionTimeoutError(RuntimeError):
    """工具调用超过允许的等待时间。"""


class ToolRuntimeOverloadedError(RuntimeError):
    """工具调用超过允许的并发数。"""


class ToolRuntime:
    """
    负责在线程池中执行工具，并应用单步 timeout。

    Executor 由外部创建并注入。
    ToolRuntime 当前不负责线程池生命周期。
    """

    def __init__(self, executor: Executor, *, capacity: int | None = None):
        if capacity is not None and capacity < 1:
            raise ValueError("capacity 必须大于等于1")
        self._executor = executor
        if capacity is None:
            self._capacity_limiter = None
        else:
            self._capacity_limiter = BoundedSemaphore(
                value=capacity
            )  # 每次调用一个新信号量就无法限制多个调用之间的总并发。信号量必须在 __init__() 中创建，并由整个 ToolRuntime 共享。

    """
    1. 校验 timeout
    2. 非阻塞获取许可
    3. 容量不足则立即拒绝
    4. 调用 executor.submit()
    5. submit 失败则立即归还许可
    6. submit 成功则注册 done callback
    7. future.result(timeout)
    8. 等待超时则尝试 cancel
    9. 不在 finally 中释放许可
    """

    def run(
        self,
        operation: Callable[[], T],  # 接收一个不需要参数的可调用对象
        *,
        timeout_seconds: float | None,
    ) -> T:
        """
        执行一个无参数 callable。

        Args:
            operation:
                已经封装好工具名称和参数的零参数函数。

            timeout_seconds:
                调用方最多等待的时间；
                None 表示无限等待。

        Returns:
            operation 的真实返回值。

        Raises:
            ValueError:
                timeout_seconds 为负数。

            ToolExecutionTimeoutError:
                Future 等待超时。
        """
        # TODO 1：拒绝负数 timeout。
        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("timeout_seconds 不能为负数")

        limiter = self._capacity_limiter
        permit_acquired = False

        if limiter is not None:
            # 使用非阻塞方式申请许可。
            permit_acquired = limiter.acquire(blocking=False)  # 背压拒绝而不是进入队列阻塞等待
            if not permit_acquired:
                raise ToolRuntimeOverloadedError("工具运行时容量已满")

        try:
            future = self._executor.submit(operation)
        except BaseException:
            if permit_acquired:
                limiter.release()
            raise

        if permit_acquired:
            assert limiter is not None

            def release_permit(_future):
                limiter.release()

            future.add_done_callback(release_permit)

        """
        Future 进入任意终态都会触发回调：

        正常返回；
        抛出异常；
        排队中被成功取消。

        这里不关心任务结果，只负责归还许可，
        """
        # #
        # # TODO 2：通过 self._executor.submit() 提交 operation。
        # future = self._executor.submit(operation)
        # TODO 3：调用 future.result(timeout=...)。
        try:
            return future.result(timeout=timeout_seconds)
        # TODO 4：捕获 FutureTimeoutError。
        except FutureTimeoutError as exc:
            # TODO 5：尝试 future.cancel()。
            future.cancel()
            # TODO 6：抛出 ToolExecutionTimeoutError，并保留异常链。
            raise ToolExecutionTimeoutError("工具执行超时") from exc
