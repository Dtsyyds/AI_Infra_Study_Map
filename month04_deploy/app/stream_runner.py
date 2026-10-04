import asyncio
from collections.abc import AsyncIterator, Callable

from .request_context import FinalizeEvent, RequestContext
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)

StreamingAgentCall = Callable[
    [str, RequestContext],
    AsyncIterator[str],
]

async def run_streaming_agent_request(
    ctx: RequestContext,
    prompt: str,
    agent_stream: StreamingAgentCall,
) -> None:
    try:
        queue = ctx.stream_queue
        if queue is None:
            raise RuntimeError("stream_queue is not configured")

        chunks: list[str] = []

        async for token in agent_stream(prompt, ctx):
            if ctx.cancel_event.is_set():
                raise asyncio.CancelledError

            # 队列满时在这里阻塞，从而向Agent生产者传播反压
            await queue.put(token)
            chunks.append(token)

    except asyncio.CancelledError:
        # 外部终结者负责清理，不能再次调用finalize_request
        raise

    except Exception as exc:
        ctx.error = str(exc)

        await finalize_request(
            ctx,
            FinalizeEvent.INTERNAL_ERROR,
            cleanup_request_resources,
        )
        return

    ctx.result = "".join(chunks)

    await finalize_request(
        ctx,
        FinalizeEvent.COMPLETED,
        cleanup_request_resources,
    )

def start_streaming_agent_request(
    ctx: RequestContext,
    prompt: str,
    agent_stream: StreamingAgentCall,
) -> asyncio.Task[None]:
    task = asyncio.create_task(
        run_streaming_agent_request(
            ctx,
            prompt,
            agent_stream,
        )
    )
    ctx.background_task = task
    return task 