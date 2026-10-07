import asyncio
from contextlib import aclosing
from collections.abc import AsyncIterator, Callable, AsyncGenerator

from .request_context import FinalizeEvent, RequestContext
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)

StreamingAgentCall = Callable[
    [str, RequestContext],
    AsyncGenerator[str, None],
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

        async with aclosing(agent_stream(prompt, ctx)) as stream:
            async for token in stream:
                if ctx.cancel_event.is_set():
                    raise asyncio.CancelledError

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