import asyncio
import math
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Request as FastAPIRequest, Response
from pydantic import BaseModel, Field

from .active_requests import ActiveRequestLimitMiddleware
from .deadline_monitor import start_deadline_monitor
from .request_context import RequestContext, RequestOutcome
from .request_runner import (
    AgentCall,
    start_agent_request,
)

from fastapi import FastAPI, Request as FastAPIRequest, Response

from .client_disconnect import start_client_disconnect_monitor

from .stream_response import AgentStreamingResponse
from .stream_runner import StreamingAgentCall

from fastapi import HTTPException

from .admission import AdmissionController

class AgentRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60.0)

class AgentResponse(BaseModel):
    request_id: str
    outcome: RequestOutcome
    result: str | None
    error: str | None   

_OUTCOME_TO_HTTP_STATUS = {
    RequestOutcome.SUCCEEDED: 200,
    RequestOutcome.TIMED_OUT: 504,
    RequestOutcome.CANCELLED: 499,
    RequestOutcome.FAILED: 500,
}

async def default_fake_agent(
    prompt: str,
    ctx: RequestContext,
) -> str:
    await asyncio.sleep(0.01)
    return f"echo:{prompt}"

async def default_fake_streaming_agent(
        prompt: str,
        ctx: RequestContext,
) -> AsyncIterator[str]:
    for token in ("收到：", prompt, "，", "处理完成。"):
        await asyncio.sleep(0.2)
        yield token

def create_app(
    agent_call: AgentCall = default_fake_agent,
    agent_stream: StreamingAgentCall = default_fake_streaming_agent,
    *,
    max_running: int = 2,
    max_waiting: int = 3,
    max_active: int = 16,
    send_timeout_seconds: float = 5.0,
    response_timeout_seconds: float = 30.0,
) -> FastAPI:
    if (
        not math.isfinite(send_timeout_seconds)
        or send_timeout_seconds <= 0
    ):
        raise ValueError(
            "send_timeout_seconds must be finite and positive"
        )

    if (
        not math.isfinite(response_timeout_seconds)
        or response_timeout_seconds <= 0
    ):
        raise ValueError(
            "response_timeout_seconds must be "
            "a finite positive number"
        )
    app = FastAPI(title="Month04 Agent Service")

    app.add_middleware(ActiveRequestLimitMiddleware, max_active=max_active)

    controller = AdmissionController(
        max_running=max_running,
        max_waiting=max_waiting,
    )

    async def limited_agent_call(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        await controller.wait_for_execution(ctx)
        return await agent_call(prompt, ctx)

    async def limited_agent_stream(
            prompt: str,
            ctx: RequestContext,
    ) -> AsyncIterator[str]:
        await controller.wait_for_execution(ctx)

        async for token in agent_stream(prompt, ctx):
            yield token

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/agent/run",
        response_model=AgentResponse,
    )
    async def run_agent(
        request: AgentRequest,
        raw_request: FastAPIRequest,
        response: Response,
    ) -> AgentResponse:
        loop = asyncio.get_running_loop()

        ctx = RequestContext(
            request_id=uuid4().hex,
            deadline=loop.time() + request.timeout_seconds,
        )

        if not await controller.try_admit(ctx):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacity_exceeded",
                    "request_id": ctx.request_id,
                },
            )

        agent_task = start_agent_request(
            ctx,
            request.prompt,
            limited_agent_call,
        )
        deadline_task = start_deadline_monitor(ctx)
        disconnect_task = start_client_disconnect_monitor(
            raw_request,
            ctx,
        )

        # 等待唯一终结者完成清理
        await ctx.finalized_event.wait()

        # 回收两个任务，避免孤儿Task
        await asyncio.gather(
            agent_task,
            deadline_task,
            disconnect_task,
            return_exceptions=True,
        )

        if ctx.outcome is None:
            raise RuntimeError(
                "request finalized without an outcome"
            )

        response.status_code = _OUTCOME_TO_HTTP_STATUS[
            ctx.outcome
        ]

        return AgentResponse(
            request_id=ctx.request_id,
            outcome=ctx.outcome,
            result=ctx.result,
            error=ctx.error,
        )

    # 流式接口：返回响应对象，由它启动生产者并逐段发送
    @app.post("/v1/agent/stream")
    async def stream_agent(
        request: AgentRequest,
    ) -> AgentStreamingResponse:
        loop = asyncio.get_running_loop()

        started_at = loop.time()

        ctx = RequestContext(
            request_id=uuid4().hex,
            deadline=started_at + request.timeout_seconds,
            # 整个流式响应的截止时间
            response_deadline=(
                started_at
                + response_timeout_seconds
            ),
            stream_queue=asyncio.Queue(maxsize=8),
        )

        admitted = await controller.try_admit(ctx)

        if not admitted:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacity_exceeded",
                    "request_id": ctx.request_id,
                },
            )

        return AgentStreamingResponse(
            ctx=ctx,
            prompt=request.prompt,
            agent_stream=limited_agent_stream,
            send_timeout_seconds=send_timeout_seconds,
        )

    return app


app = create_app()