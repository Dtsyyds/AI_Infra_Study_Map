import asyncio
from typing import Protocol

from .request_context import FinalizeEvent, RequestContext
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)

class DisconnectProbe(Protocol):
    async def is_disconnected(self) -> bool:
        ...

async def monitor_client_disconnect(
    request: DisconnectProbe,
    ctx: RequestContext,
    poll_interval: float = 0.05,
) -> bool:
    """
    True：客户端断开获得终结权。
    False：请求已经由其他事件终结。
    """

    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    while True:
        if ctx.finalized_event.is_set():
            return False

        if await request.is_disconnected():
            return await finalize_request(
                ctx,
                FinalizeEvent.CLIENT_DISCONNECTED,
                cleanup_request_resources,
            )

        try:
            # 请求结束后立即退出，不留下监视Task
            await asyncio.wait_for(
                ctx.finalized_event.wait(),
                timeout=poll_interval,
            )
            return False
        except TimeoutError:
            continue    

def start_client_disconnect_monitor(
    request: DisconnectProbe,
    ctx: RequestContext,
    poll_interval: float = 0.05,
) -> asyncio.Task[bool]:
    task = asyncio.create_task(
        monitor_client_disconnect(
            request,
            ctx,
            poll_interval,
        )
    )
    ctx.disconnect_task = task
    return task 