import asyncio

from .request_context import FinalizeEvent, RequestContext
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)

async def monitor_deadline(ctx: RequestContext) -> bool:
    """
    返回True：Deadline获得终结权。
    返回False：请求在Deadline前已经被其他事件终结。
    """

    if ctx.finalized_event.is_set():
        return False

    loop = asyncio.get_running_loop()
    remaining = ctx.deadline - loop.time()

    if remaining <= 0:
        return await finalize_request(
            ctx,
            FinalizeEvent.DEADLINE_EXCEEDED,
            cleanup_request_resources,
        )

    try:
        # 请求提前结束时立即退出，不让监视任务一直sleep到Deadline
        await asyncio.wait_for(
            ctx.finalized_event.wait(),
            timeout=remaining,
        )
        return False

    except TimeoutError:
        return await finalize_request(
            ctx,
            FinalizeEvent.DEADLINE_EXCEEDED,
            cleanup_request_resources,
        )

def start_deadline_monitor(
    ctx: RequestContext,
) -> asyncio.Task[bool]:
    task = asyncio.create_task(monitor_deadline(ctx))
    ctx.deadline_task = task
    return task