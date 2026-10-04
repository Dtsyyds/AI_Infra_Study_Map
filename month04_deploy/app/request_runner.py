import asyncio
from collections.abc import Awaitable, Callable

from .request_context import FinalizeEvent, RequestContext
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)


AgentCall = Callable[[str, RequestContext], Awaitable[str]]
"""
Callable[[参数类型列表], 返回类型]：表示可调用对象（函数/方法）
参数：str（prompt）、RequestContext（上下文）

返回：Awaitable[str]，即可被 await 的对象（如协程），最终得到 str

也就是说 agent_call 是一个"异步函数"，签名为 async def f(prompt: str, ctx: RequestContext) -> str
"""

# 协程函数，用于处理 agent 请求
async def run_agent_request(
    ctx: RequestContext,
    prompt: str,
    agent_call: AgentCall,
) -> None:
    try:
        result = await agent_call(prompt, ctx)
    except asyncio.CancelledError:
        """
        当任务被 task.cancel() 取消时，会抛出 asyncio.CancelledError

        必须重新 raise：让取消语义向上传播，否则 asyncio 会误以为任务正常结束

        不调用 finalize_request：注释说明原因——取消路径下的清理工作由外部取消者负责（避免重复清理，比如重复 release 资源、重复写状态等导致报错）
        """
        # 非常重要：不能在这里再次调用finalize_request
        raise

    except Exception as exc:
        ctx.error = str(exc)

        await finalize_request(
            ctx,
            FinalizeEvent.INTERNAL_ERROR,
            cleanup_request_resources,
        )
        return

    ctx.result = result

    await finalize_request(
        ctx,
        FinalizeEvent.COMPLETED,
        cleanup_request_resources,
    )

def start_agent_request(
    ctx: RequestContext,
    prompt: str,
    agent_call: AgentCall,
) -> asyncio.Task[None]:
    task = asyncio.create_task(
        run_agent_request(ctx, prompt, agent_call)
    )
    ctx.background_task = task
    return task
