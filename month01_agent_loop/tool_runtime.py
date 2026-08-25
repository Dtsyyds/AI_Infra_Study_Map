from collections.abc import Callable
from concurrent.futures import (
    Executor,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from typing import TypeVar

T = TypeVar("T")


class ToolExecutionTimeoutError(RuntimeError):
    """工具调用超过允许的等待时间。"""


class ToolRuntime:
    """
    负责在线程池中执行工具，并应用单步 timeout。

    Executor 由外部创建并注入。
    ToolRuntime 当前不负责线程池生命周期。
    """

    def __init__(self, executor: Executor):
        self._executor = executor

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
        # TODO 2：通过 self._executor.submit() 提交 operation。
        future = self._executor.submit(operation)
        # TODO 3：调用 future.result(timeout=...)。
        try:
            return future.result(timeout=timeout_seconds)
        # TODO 4：捕获 FutureTimeoutError。
        except FutureTimeoutError as exc:
            # TODO 5：尝试 future.cancel()。
            future.cancel()
            # TODO 6：抛出 ToolExecutionTimeoutError，并保留异常链。
            raise ToolExecutionTimeoutError("工具执行超时") from exc
