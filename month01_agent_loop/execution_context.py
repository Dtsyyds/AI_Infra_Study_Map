from collections.abc import Callable
from threading import Event
from time import monotonic


class TaskTimeoutError(RuntimeError):
    """任务超过总截止时间。"""


class TaskCancelledError(RuntimeError):
    """任务收到取消请求。"""


class ExecutionContext:
    """
    保存一次 Agent 任务的执行控制信息。

    ExecutionContext 生命周期跟随单次 Agent 任务，
    不应该保存到 Agent 实例级历史 memory 中。
    """

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        clock: Callable[[], float] = monotonic,
    ):
        """
        Args:
            timeout_seconds:
                整个任务最多允许运行多少秒。
                None 表示没有 deadline。

            clock:
                获取单调时间的函数。
                生产环境默认使用 time.monotonic；
                测试中可以注入 FakeClock。
        """
        # TODO 1：
        # timeout_seconds 小于 0 时抛出 ValueError。
        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("timeout_seconds 必须大于等于0")

        # TODO 2：
        # 保存 clock。
        self._clock = clock  # 保存 clock 函数到实例变量中

        start_time = clock()

        # TODO 3：
        # 如果 timeout_seconds 为 None：
        #     deadline_monotonic = None
        # 否则：
        #     deadline_monotonic = clock() + timeout_seconds
        if timeout_seconds is None:
            self._deadline_monotonic = None
        else:
            self._deadline_monotonic = start_time + timeout_seconds

        # TODO 4：
        # 创建 threading.Event 作为取消信号。
        self._cancelled = Event()

    # 类中被定义为一个 @property（属性）访问时就像访问实例变量一样，直接 self.property，不需要括号
    @property
    def deadline_monotonic(self) -> float | None:
        """返回任务的绝对截止时间。"""
        # TODO
        return self._deadline_monotonic

    def remaining_seconds(self) -> float | None:
        """
        返回任务剩余时间。

        没有 deadline 时返回 None；
        已过期时返回 0，而不是负数。
        """
        # TODO：
        # max(0.0, deadline - 当前时间)
        deadline = self.deadline_monotonic
        if deadline is None:
            return None
        else:
            return max(0.0, deadline - self._clock())  # 调用保存的函数

    def is_timed_out(self) -> bool:
        """判断任务是否已经到达 deadline。"""
        # TODO
        remaining = self.remaining_seconds()
        if remaining is None:
            return False
        else:
            return remaining <= 0

    def cancel(self) -> None:
        """设置取消信号。"""
        # TODO
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        """判断是否收到取消信号。"""
        # TODO
        return self._cancelled.is_set()

    def check_active(self) -> None:
        """
        检查当前任务是否还能继续运行。

        约定优先级：
        1. 已取消 → TaskCancelledError
        2. 已超时 → TaskTimeoutError
        3. 否则正常返回
        """
        # TODO
        if self.is_cancelled():
            raise TaskCancelledError("任务已被取消")
        elif self.is_timed_out():
            raise TaskTimeoutError("任务已超过截止时间")

        return None

    def effective_timeout_seconds(
        self,
        *,
        step_timeout_seconds: float | None,
    ) -> float | None:
        """
        计算当前步骤真正可以使用的时间。

        None 表示该层没有设置时间限制。
        """
        if step_timeout_seconds is not None and step_timeout_seconds < 0:
            raise ValueError("step_timeout_seconds 必须大于等于0")
        remaining = self.remaining_seconds()

        if remaining is None and step_timeout_seconds is None:
            return None
        elif remaining is None:
            return step_timeout_seconds
        elif step_timeout_seconds is None:
            return remaining
        else:
            return min(step_timeout_seconds, remaining)
