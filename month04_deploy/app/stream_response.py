import asyncio
import logging

import anyio
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from .deadline_monitor import start_deadline_monitor
from .request_context import (
    FinalizeEvent,
    RequestContext,
    TransportOutcome,
)
from .request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)
from .send_timeout import SendTimeoutError, make_timed_send
from .stream_consumer import iter_stream_jsonl
from .stream_runner import (
    StreamingAgentCall,
    start_streaming_agent_request,
)


logger = logging.getLogger(__name__)

class ResponseDeadlineExceeded(RuntimeError):
    """流式响应的总时间预算已经耗尽。"""


class AgentStreamingResponse(StreamingResponse):
    def __init__(
        self,
        ctx: RequestContext,
        prompt: str,
        agent_stream: StreamingAgentCall,
        send_timeout_seconds: float = 5.0,
    ) -> None:
        self.ctx = ctx
        self.prompt = prompt
        self.agent_stream = agent_stream
        self.send_timeout_seconds = send_timeout_seconds
        self.consumer = iter_stream_jsonl(ctx)

        super().__init__(
            content=self.consumer,
            media_type="application/x-ndjson",
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        ctx = self.ctx

        timed_send = make_timed_send(
            send,
            timeout_seconds=self.send_timeout_seconds,
        )

        final_body_sent = False
        abort_event = FinalizeEvent.CLIENT_DISCONNECTED

        async def observed_send(message: Message) -> None:
            nonlocal final_body_sent, abort_event

            try:
                await timed_send(message)
            except SendTimeoutError as exc:
                # 在异常经过框架包装之前记录故障来源
                ctx.transport_outcome = (
                    TransportOutcome.SEND_TIMED_OUT
                )
                ctx.transport_error = str(exc)
                abort_event = FinalizeEvent.SEND_TIMEOUT
                raise

            # 必须等最后一次send成功返回，才能标记发送完成
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                final_body_sent = True

        producer_task = start_streaming_agent_request(
            ctx,
            self.prompt,
            self.agent_stream,
        )
        deadline_task = start_deadline_monitor(ctx)

        try:
            remaining = None

            if ctx.response_deadline is not None:
                remaining = (
                    ctx.response_deadline
                    - asyncio.get_running_loop().time()
                )

                if remaining <= 0:
                    raise ResponseDeadlineExceeded(
                        "response deadline exceeded "
                        "before streaming started"
                    )

            # 同时覆盖：
            # 等待 Token、发送 Token、发送最终响应消息。
            with anyio.move_on_after(
                remaining
            ) as response_scope:
                await super().__call__(
                    scope,
                    receive,
                    observed_send,
                )

            if (
                response_scope.cancelled_caught
                and not final_body_sent
            ):
                raise ResponseDeadlineExceeded(
                    "response deadline exceeded"
                )

        except ResponseDeadlineExceeded as exc:
            ctx.transport_outcome = (
                TransportOutcome.RESPONSE_TIMED_OUT
            )
            ctx.transport_error = str(exc)

            # 仅在业务尚未终结时，由原 finally
            # 通过这个事件完成终结和资源清理。
            abort_event = (
                FinalizeEvent.RESPONSE_DEADLINE_EXCEEDED
            )

            raise

        except ClientDisconnect as exc:
            if ctx.transport_outcome is None:
                ctx.transport_outcome = (
                    TransportOutcome.DISCONNECTED
                )
                ctx.transport_error = str(exc)
            raise

        except asyncio.CancelledError:
            if ctx.transport_outcome is None:
                ctx.transport_outcome = (
                    TransportOutcome.CANCELLED
                )
            raise

        except Exception as exc:
            # 发送超时已由observed_send准确记录，
            # 不要在这里覆盖成内部错误。
            if (
                ctx.transport_outcome
                is not TransportOutcome.SEND_TIMED_OUT
            ):
                ctx.transport_outcome = TransportOutcome.FAILED
                ctx.transport_error = str(exc)
                abort_event = FinalizeEvent.INTERNAL_ERROR
            raise

        else:
            if ctx.transport_outcome is None:
                ctx.transport_outcome = (
                    TransportOutcome.SENT
                    if final_body_sent
                    else TransportOutcome.DISCONNECTED
                )

        finally:
            with anyio.CancelScope(shield=True):
                try:
                    if not ctx.finalized_event.is_set():
                        await finalize_request(
                            ctx,
                            abort_event,
                            cleanup_request_resources,
                        )
                finally:
                    try:
                        await self.consumer.aclose()
                    finally:
                        await asyncio.gather(
                            producer_task,
                            deadline_task,
                            return_exceptions=True,
                        )

                        if ctx.transport_outcome in (
                            TransportOutcome.SEND_TIMED_OUT,
                            TransportOutcome.RESPONSE_TIMED_OUT,
                        ):
                            logger.warning(
                                "stream send timeout "
                                "request_id=%s "
                                "outcome=%s "
                                "transport_outcome=%s"
                                "transport_error=%s",
                                ctx.request_id,
                                (
                                    ctx.outcome.value
                                    if ctx.outcome is not None
                                    else None
                                ),
                                ctx.transport_outcome.value,
                                ctx.transport_error,
                            )