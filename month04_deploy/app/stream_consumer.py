import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from .request_context import RequestContext

def encode_json_line(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

async def iter_stream_jsonl(
    ctx: RequestContext,
) -> AsyncIterator[bytes]:
    """
    先排空Token队列，再发送最终done事件。

    不使用结束哨兵，生产结束由finalized_event表示。
    """

    queue = ctx.stream_queue
    if queue is None:
        raise RuntimeError("stream_queue is not configured")

    get_task: asyncio.Task[str] | None = None
    finalized_task: asyncio.Task[bool] | None = None

    try:
        while True:
            try:
                # 队列已有Token时直接获取，避免创建额外Task
                token = queue.get_nowait()

            except asyncio.QueueEmpty:
                # 队列为空并且生产者已结束，退出Token循环
                if ctx.finalized_event.is_set():
                    break

                get_task = asyncio.create_task(queue.get())
                finalized_task = asyncio.create_task(
                    ctx.finalized_event.wait()
                )

                done, pending = await asyncio.wait(
                    {get_task, finalized_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # 防止辅助Task泄漏
                for task in pending:
                    task.cancel()

                await asyncio.gather(
                    *pending,
                    return_exceptions=True,
                )

                if get_task in done:
                    token = get_task.result()
                else:
                    # finalized_event先到，回到循环检查队列中
                    # 是否还有未发送Token
                    get_task = None
                    finalized_task = None
                    continue

                get_task = None
                finalized_task = None

            try:
                yield encode_json_line(
                    {
                        "type": "token",
                        "data": token,
                    }
                )
            finally:
                queue.task_done()

        if ctx.outcome is None:
            raise RuntimeError(
                "finalized stream has no request outcome"
            )

        yield encode_json_line(
            {
                "type": "done",
                "request_id": ctx.request_id,
                "outcome": ctx.outcome.value,
                "error": ctx.error,
            }
        )

    finally:
        # 消费生成器被取消时，回收正在等待的辅助Task
        helper_tasks = [
            task
            for task in (get_task, finalized_task)
            if task is not None and not task.done()
        ]

        for task in helper_tasks:
            task.cancel()

        if helper_tasks:
            await asyncio.gather(
                *helper_tasks,
                return_exceptions=True,
            )