import asyncio
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

from month04_deploy.app.api import create_app
from month04_deploy.app.request_context import (
    FinalizeEvent,
    RequestOutcome,
    RequestPhase,
)

@asynccontextmanager
async def live_server(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            loop="asyncio",
            http="h11",
            ws="none",
            log_level="error",
            timeout_graceful_shutdown=2,
        )
    )
    server_task = asyncio.create_task(
        server.serve(sockets=[sock])
    )

    async def wait_until_started():
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("server exited before startup")

            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(
            wait_until_started(),
            timeout=5,
        )
        yield f"http://127.0.0.1:{port}"

    finally:
        server.should_exit = True

        try:
            await asyncio.wait_for(
                asyncio.shield(server_task),
                timeout=5,
            )
        finally:
            if not server_task.done():
                server_task.cancel()

            await asyncio.gather(
                server_task,
                return_exceptions=True,
            )
            sock.close()

@pytest.mark.asyncio
async def test_http_disconnect_cancels_streaming_agent():
    never_finish = asyncio.Event()
    agent_exited = asyncio.Event()
    response_finished = asyncio.Event()
    observed_contexts = []

    async def slow_stream(prompt, ctx):
        observed_contexts.append(ctx)

        try:
            yield "A"

            # 发出第一个Token后一直等待
            await never_finish.wait()

            yield "不应该出现"
        finally:
            agent_exited.set()

    app = create_app(agent_stream=slow_stream)

    # 观察整个HTTP请求处理是否退出，
    # 包括AgentStreamingResponse中的finally清理
    async def observed_app(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            if (
                scope["type"] == "http"
                and scope["path"] == "/v1/agent/stream"
            ):
                response_finished.set()

    async with live_server(observed_app) as base_url:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=3,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                "/v1/agent/stream",
                json={
                    "prompt": "hello",
                    "timeout_seconds": 30,
                },
            ) as response:
                assert response.status_code == 200

                lines = response.aiter_lines()
                first_line = await asyncio.wait_for(
                    anext(lines),
                    timeout=3,
                )

                assert json.loads(first_line) == {
                    "type": "token",
                    "data": "A",
                }

            # 离开client.stream上下文：
            # 提前关闭尚未读完的响应，触发真实连接断开

            await asyncio.wait_for(
                response_finished.wait(),
                timeout=3,
            )

            ctx = observed_contexts[0]

            assert agent_exited.is_set() is True
            assert ctx.background_task.cancelled() is True
            assert ctx.deadline_task.done() is True

            assert (
                ctx.finalize_winner
                is FinalizeEvent.CLIENT_DISCONNECTED
            )
            assert ctx.outcome is RequestOutcome.CANCELLED
            assert ctx.cancel_event.is_set() is True

            assert ctx.phase is RequestPhase.FINALIZED
            assert ctx.finalized_event.is_set() is True