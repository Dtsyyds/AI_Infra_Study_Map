import math

import anyio
from starlette.types import Message, Send


class SendTimeoutError(RuntimeError):
    """应用配置的单次发送等待时间已耗尽。"""


def make_timed_send(
    send: Send,
    timeout_seconds: float,
) -> Send:
    if (
        not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError(
            "timeout_seconds must be finite and positive"
        )

    async def timed_send(message: Message) -> None:
        with anyio.move_on_after(
            timeout_seconds
        ) as timeout_scope:
            await send(message)

        # 只有当前超时作用域触发并处理了取消，
        # 才认定是本层的发送超时。
        if timeout_scope.cancelled_caught:
            raise SendTimeoutError(
                f"send exceeded {timeout_seconds} seconds"
            )

    return timed_send