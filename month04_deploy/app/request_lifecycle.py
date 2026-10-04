import asyncio
from collections.abc import Awaitable, Callable

from month04_deploy.app.request_context import (
    RequestContext,
    FinalizeEvent,
    RequestPhase,
    RequestOutcome
)

_EVENT_TO_OUTCOME = {
    FinalizeEvent.COMPLETED: RequestOutcome.SUCCEEDED,
    FinalizeEvent.CLIENT_DISCONNECTED: RequestOutcome.CANCELLED,
    FinalizeEvent.DEADLINE_EXCEEDED: RequestOutcome.TIMED_OUT,
    FinalizeEvent.INTERNAL_ERROR: RequestOutcome.FAILED,
    FinalizeEvent.SEND_TIMEOUT: RequestOutcome.CANCELLED,
    FinalizeEvent.RESPONSE_DEADLINE_EXCEEDED: RequestOutcome.TIMED_OUT,
}

CleanupHook = Callable[[RequestContext], Awaitable[None]]
 
async def try_claim_finalize(
        ctx: RequestContext,
        event: FinalizeEvent,
        ) -> bool:
    """
    状态检查和迁移必须在同一个 asyncio.Lock 临界区内；
    只有一个调用者返回 True；
    锁只保护状态迁移，不能在持锁时等待外部任务退出；
    失败者等待 finalized_event 或直接读取终态；
    Deadline 使用单调时钟 loop.time()；
    状态只能按照：

    尝试获得请求终结权
    True：当前调用者是唯一终结者。
    False：其他调用者已经获得终结权。
    """

    async with ctx.state_lock:
        if ctx.phase != RequestPhase.ACTIVE:
            return False
        loop = asyncio.get_running_loop()
        now = loop.time()
        effective_event = event

        if(
            event is FinalizeEvent.COMPLETED
            and now >= ctx.deadline
        ):
            effective_event = FinalizeEvent.DEADLINE_EXCEEDED

        if(
            event is FinalizeEvent.DEADLINE_EXCEEDED
            and now < ctx.deadline
        ):
            return False

        if (
            event is FinalizeEvent.RESPONSE_DEADLINE_EXCEEDED
            and (
                ctx.response_deadline is None
                or now < ctx.response_deadline
            )
        ):
            return False

        ctx.phase = RequestPhase.FINALIZING
        ctx.finalize_winner = effective_event
        ctx.outcome = _EVENT_TO_OUTCOME[effective_event]

    return True

async def finalize_request(
        ctx: RequestContext,
        event: FinalizeEvent,
        cleanup: CleanupHook
) -> bool:
    """
    返回True：当前调用者是终结者，并执行了清理。
    返回False：其他调用者是终结者，当前调用者等待其清理结束。
    """
    claimed = await try_claim_finalize(ctx, event)

    if not claimed:
        # False也可能表示截止时间事件尚未生效，此时没有终结者可等。
        async with ctx.state_lock:
            wait_for_winner = ctx.phase is RequestPhase.FINALIZING
        # 不能持有state_lock等待，否则终结者无法发布FINALIZED
        if wait_for_winner:
            await ctx.finalized_event.wait()
        return False

    try:
        # 非成功终态需要向下游传播取消
        if ctx.outcome is not RequestOutcome.SUCCEEDED:
            ctx.cancel_event.set()

        # 这里不能持有state_lock
        await cleanup(ctx)
    finally:
        async with ctx.state_lock:
            ctx.phase = RequestPhase.FINALIZED
            ctx.finalized_event.set()

    return True

async def cleanup_request_resources(
        ctx: RequestContext,
) -> None:
    task = ctx.background_task
    current_task = asyncio.current_task()

    # 非成功请求需要终止仍在运行的后台任务
    if (
        ctx.outcome is not RequestOutcome.SUCCEEDED
        and task is not None
        and task is not current_task
        and not task.done()
    ):
        task.cancel()

        try:
            # cancel()只是发出取消请求，await确认任务真正退出
            await task
        except asyncio.CancelledError:
            pass

    # 先释放队列容量
    if (
        ctx.queue_slot_acquired
        and ctx.queue_semaphore is not None
    ):
        ctx.queue_semaphore.release()
        ctx.queue_slot_acquired = False

    # Permit最后释放
    if (
        ctx.permit_acquired
        and ctx.execution_semaphore is not None
    ):
        ctx.execution_semaphore.release()
        ctx.permit_acquired = False
