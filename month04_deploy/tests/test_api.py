import asyncio

import json
import pytest
from httpx import ASGITransport, AsyncClient

from month04_deploy.app.api import create_app
from month04_deploy.app.request_context import (
    RequestContext,
    FinalizeEvent,
    RequestOutcome,
    TransportOutcome,
)

from month04_deploy.app.send_timeout import SendTimeoutError
from month04_deploy.app.stream_response import (
    ResponseDeadlineExceeded,
)


@pytest.mark.asyncio
async def test_default_app_run_smoke():
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/run",
            json={
                "prompt": "hello",
                "timeout_seconds": 1,
            },
        )

    body = response.json()

    assert response.status_code == 200
    assert body["outcome"] == "succeeded"
    assert body["result"] == "echo:hello"
    assert body["error"] is None


@pytest.mark.asyncio
async def test_api_success():
    async def fake_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        await asyncio.sleep(0)
        return f"answer:{prompt}"

    app = create_app(fake_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/run",
            json={
                "prompt": "hello",
                "timeout_seconds": 1,
            },
        )

    body = response.json()

    assert response.status_code == 200
    assert body["outcome"] == "succeeded"
    assert body["result"] == "answer:hello"
    assert body["error"] is None
    assert body["request_id"]

@pytest.mark.asyncio
async def test_api_timeout():
    """
    fake_agent等待1秒，请求Deadline设置为0.01秒。

    断言：
    status_code == 504
    outcome == "timed_out"
    result is None
    """
    async def faker_agent(
            prompt: str,
            ctx: RequestContext,
    ) -> str:
        await asyncio.sleep(1)
        return f"answer:{prompt}"

    app = create_app(faker_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/run",
            json={
                "prompt": "hello",
                "timeout_seconds": 0.01,
            },
        )

    body = response.json()

    assert response.status_code == 504
    assert body["outcome"] == "timed_out"
    assert body["result"] is None

@pytest.mark.asyncio
async def test_api_internal_error():
    """
    fake_agent抛出RuntimeError("fake failure")。

    断言：
    status_code == 500
    outcome == "failed"
    error == "fake failure"
    """

    async def fake_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        raise RuntimeError("fake failure")

    app = create_app(fake_agent)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/run",
            json={
                "prompt": "hello",
                "timeout_seconds": 1,
            },
        )

    body = response.json()

    assert response.status_code == 500
    assert body["outcome"] == "failed"
    assert body["error"] == "fake failure"


@pytest.mark.asyncio
async def test_api_stream_success():
    async def fake_stream(prompt, ctx):
        yield "A"
        await asyncio.sleep(0)
        yield "B"

    app = create_app(agent_stream=fake_stream)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/stream",
            json={
                "prompt": "hello",
                "timeout_seconds": 1,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/x-ndjson"
    )

    events = [
        json.loads(line)
        for line in response.text.splitlines()
    ]

    assert len(events) == 3
    assert events[:2] == [
        {"type": "token", "data": "A"},
        {"type": "token", "data": "B"},
    ]

    done = events[2]
    assert done["type"] == "done"
    assert done["request_id"]
    assert done["outcome"] == "succeeded"
    assert done["error"] is None

@pytest.mark.asyncio
async def test_api_stream_timeout_after_partial_output():
    never_finish = asyncio.Event()
    observed_contexts = []

    async def slow_stream(prompt, ctx):
        observed_contexts.append(ctx)

        yield "A"
        yield "B"

        # 已经输出部分内容，但始终无法正常完成
        await never_finish.wait()

        yield "不应该出现"

    app = create_app(agent_stream=slow_stream)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/v1/agent/stream",
                json={
                    "prompt": "hello",
                    "timeout_seconds": 0.05,
                },
            ),
            timeout=2.0,
        )

    events = [
        json.loads(line)
        for line in response.text.splitlines()
    ]

    # 流式响应头已经发出，超时通过done事件表达
    assert response.status_code == 200

    # 保留部分输出，且没有多余Token或重复done
    assert len(events) == 3
    assert events[:2] == [
        {"type": "token", "data": "A"},
        {"type": "token", "data": "B"},
    ]

    ctx = observed_contexts[0]

    assert events[2] == {
        "type": "done",
        "request_id": ctx.request_id,
        "outcome": "timed_out",
        "error": None,
    }

    # 不仅验证输出协议，还验证后台生产任务确实停止
    assert ctx.cancel_event.is_set() is True
    assert ctx.background_task.cancelled() is True
    assert ctx.deadline_task.done() is True
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_api_stream_error_after_partial_output():
    async def failing_stream(prompt, ctx):
        yield "A"
        raise RuntimeError("stream failed")

    app = create_app(agent_stream=failing_stream)

    # 仿照上一个测试：
    # 1. 创建AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/v1/agent/stream",
                json={
                    "prompt": "hello",
                    "timeout_seconds": 1,
                },
            ),
            timeout=2.0,
        )

    events = [
        json.loads(line)
        for line in response.text.splitlines()
    ]

    assert response.status_code == 200
    assert len(events) == 2

    assert events[0] == {
        "type": "token",
        "data": "A",
    }

    assert events[1]["type"] == "done"
    assert events[1]["request_id"]
    assert events[1]["outcome"] == "failed"
    assert events[1]["error"] == "stream failed"
    # 2. 请求 /v1/agent/stream，timeout_seconds设为1
    # 3. 按行解析response.text
    # 4. 验证下面的结果

@pytest.mark.asyncio
async def test_api_rejects_overload_then_recovers():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    executed_prompts = []

    async def controlled_agent(prompt, ctx):
        executed_prompts.append(prompt)

        if prompt == "first":
            first_started.set()
            await release_first.wait()

        return f"answer:{prompt}"

    app = create_app(
        agent_call=controlled_agent,
        max_running=1,
        max_waiting=0,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_request = asyncio.create_task(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "first",
                    "timeout_seconds": 10,
                },
            )
        )

        try:
            await asyncio.wait_for(
                first_started.wait(),
                timeout=2,
            )

            # first仍在执行，且不允许排队
            rejected = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 10,
                    },
                ),
                timeout=2,
            )

            assert rejected.status_code == 503
            assert rejected.json()["detail"]["code"] == (
                "capacity_exceeded"
            )

            # 被拒绝的请求不能进入Agent
            assert executed_prompts == ["first"]

            # 让first完成并释放Permit
            release_first.set()

            first_response = await asyncio.wait_for(
                asyncio.shield(first_request),
                timeout=2,
            )
            assert first_response.status_code == 200

            # 容量恢复，新请求能够执行
            recovered = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "third",
                        "timeout_seconds": 10,
                    },
                ),
                timeout=2,
            )

            assert recovered.status_code == 200
            assert recovered.json()["result"] == "answer:third"
            assert executed_prompts == ["first", "third"]

        finally:
            release_first.set()

            await asyncio.wait_for(
                asyncio.gather(
                    first_request,
                    return_exceptions=True,
                ),
                timeout=2,
            )

@pytest.mark.asyncio
async def test_api_queue_timeout_releases_waiting_slot():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    executed_prompts = []

    async def controlled_agent(prompt, ctx):
        executed_prompts.append(prompt)

        if prompt == "first":
            first_started.set()
            await release_first.wait()

        return f"answer:{prompt}"

    app = create_app(
        agent_call=controlled_agent,
        max_running=1,
        max_waiting=1,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_request = asyncio.create_task(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "first",
                    "timeout_seconds": 10,
                },
            )
        )

        try:
            await asyncio.wait_for(
                first_started.wait(),
                timeout=2,
            )

            # first持续占用执行Permit。
            # second、third依次入队，各自在排队期间超时。
            for prompt in ("second", "third"):
                response = await asyncio.wait_for(
                    client.post(
                        "/v1/agent/run",
                        json={
                            "prompt": prompt,
                            "timeout_seconds": 0.05,
                        },
                    ),
                    timeout=2,
                )

                body = response.json()

                assert response.status_code == 504
                assert body["outcome"] == "timed_out"
                assert body["result"] is None

                # 排队超时的请求没有进入Agent
                assert executed_prompts == ["first"]

                # 排队请求超时不应影响正在执行的first
                assert first_request.done() is False

            # 释放first，让它正常完成
            release_first.set()

            first_response = await asyncio.wait_for(
                asyncio.shield(first_request),
                timeout=2,
            )

            assert first_response.status_code == 200
            assert first_response.json()["outcome"] == "succeeded"

            # 确认后续请求仍能正常执行
            recovered = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "fourth",
                        "timeout_seconds": 10,
                    },
                ),
                timeout=2,
            )

            assert recovered.status_code == 200
            assert recovered.json()["result"] == "answer:fourth"
            assert executed_prompts == ["first", "fourth"]

        finally:
            release_first.set()

            await asyncio.wait_for(
                asyncio.gather(
                    first_request,
                    return_exceptions=True,
                ),
                timeout=2,
            )

@pytest.mark.asyncio
@pytest.mark.parametrize("max_waiting", [0, 1])
async def test_stream_shares_capacity_with_run(max_waiting):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    stream_calls = []

    async def blocking_agent(prompt, ctx):
        first_started.set()
        await release_first.wait()
        return "first finished"

    async def fake_stream(prompt, ctx):
        stream_calls.append(prompt)
        yield "A"

    app = create_app(
        agent_call=blocking_agent,
        agent_stream=fake_stream,
        max_running=1,
        max_waiting=max_waiting,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_request = asyncio.create_task(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "first",
                    "timeout_seconds": 10,
                },
            )
        )

        try:
            await asyncio.wait_for(
                first_started.wait(),
                timeout=2,
            )

            # 非流式请求占住唯一执行Permit。
            # 连续两次检查，也验证排队超时后名额能恢复。
            for prompt in ("second", "third"):
                response = await asyncio.wait_for(
                    client.post(
                        "/v1/agent/stream",
                        json={
                            "prompt": prompt,
                            "timeout_seconds": 0.05,
                        },
                    ),
                    timeout=2,
                )

                if max_waiting == 0:
                    assert response.status_code == 503
                    assert response.json()["detail"]["code"] == (
                        "capacity_exceeded"
                    )
                else:
                    assert response.status_code == 200

                    events = [
                        json.loads(line)
                        for line in response.text.splitlines()
                    ]

                    # 排队期间没运行Agent，因此没有Token事件
                    assert len(events) == 1
                    assert events[0]["type"] == "done"
                    assert events[0]["outcome"] == "timed_out"

                assert stream_calls == []
                assert first_request.done() is False

            # 非流式请求完成，归还执行Permit
            release_first.set()

            first_response = await asyncio.wait_for(
                asyncio.shield(first_request),
                timeout=2,
            )
            assert first_response.status_code == 200

            # 流式请求现在可以执行
            recovered = await asyncio.wait_for(
                client.post(
                    "/v1/agent/stream",
                    json={
                        "prompt": "fourth",
                        "timeout_seconds": 1,
                    },
                ),
                timeout=2,
            )

            events = [
                json.loads(line)
                for line in recovered.text.splitlines()
            ]

            assert recovered.status_code == 200
            assert len(events) == 2
            assert events[0] == {"type": "token", "data": "A"}
            assert events[1]["type"] == "done"
            assert events[1]["outcome"] == "succeeded"
            assert stream_calls == ["fourth"]

        finally:
            release_first.set()

            await asyncio.wait_for(
                asyncio.gather(
                    first_request,
                    return_exceptions=True,
                ),
                timeout=2,
            )

@pytest.mark.asyncio
async def test_active_slot_is_held_until_response_finishes():
    send_blocked = asyncio.Event()
    allow_send = asyncio.Event()
    observed_contexts = []

    async def fast_stream(prompt, ctx):
        observed_contexts.append(ctx)
        yield "A"

    async def fake_agent(prompt, ctx):
        return f"answer:{prompt}"

    app = create_app(
        agent_call=fake_agent,
        agent_stream=fast_stream,
        max_running=1,
        max_waiting=0,
        max_active=1,
    )

    # 测试包装器：暂停流式响应的数据发送
    async def slow_transport_app(scope, receive, send):
        async def controlled_send(message):
            if (
                scope["type"] == "http"
                and scope["path"] == "/v1/agent/stream"
                and message["type"] == "http.response.body"
                and message.get("more_body", False)
                and message.get("body")
            ):
                send_blocked.set()
                await allow_send.wait()

            await send(message)

        await app(scope, receive, controlled_send)

    async with AsyncClient(
        transport=ASGITransport(app=slow_transport_app),
        base_url="http://test",
    ) as client:
        first_request = asyncio.create_task(
            client.post(
                "/v1/agent/stream",
                json={
                    "prompt": "first",
                    "timeout_seconds": 5,
                },
            )
        )

        try:
            await asyncio.wait_for(
                send_blocked.wait(),
                timeout=2,
            )

            ctx = observed_contexts[0]

            await asyncio.wait_for(
                ctx.finalized_event.wait(),
                timeout=2,
            )

            # Agent已完成，执行Permit已经归还
            assert ctx.permit_acquired is False

            # 但发送仍被阻塞，整个响应尚未完成
            assert first_request.done() is False

            # 即使执行容量空闲，活跃请求容量仍然已满
            rejected = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 1,
                    },
                ),
                timeout=2,
            )

            assert rejected.status_code == 503
            assert rejected.json()["detail"]["code"] == (
                "active_request_limit"
            )

            # 允许发送结束，随后才归还活跃请求名额
            allow_send.set()

            first_response = await asyncio.wait_for(
                asyncio.shield(first_request),
                timeout=2,
            )
            assert first_response.status_code == 200

            recovered = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "third",
                        "timeout_seconds": 1,
                    },
                ),
                timeout=2,
            )

            assert recovered.status_code == 200
            assert recovered.json()["result"] == "answer:third"

        finally:
            allow_send.set()

            await asyncio.wait_for(
                asyncio.gather(
                    first_request,
                    return_exceptions=True,
                ),
                timeout=2,
            )

def exception_leaves(exc):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from exception_leaves(child)
    else:
        yield exc

@pytest.mark.asyncio
@pytest.mark.parametrize("producer_finishes", [True, False])
async def test_stream_send_timeout_preserves_business_state(
    producer_finishes,
):
    never_send = asyncio.Event()
    never_finish_agent = asyncio.Event()
    agent_exited = asyncio.Event()
    observed_contexts = []

    async def fake_stream(prompt, ctx):
        observed_contexts.append(ctx)

        try:
            yield "A"

            if not producer_finishes:
                await never_finish_agent.wait()
        finally:
            agent_exited.set()

    async def fake_agent(prompt, ctx):
        return "recovered"

    app = create_app(
        agent_call=fake_agent,
        agent_stream=fake_stream,
        max_running=1,
        max_waiting=0,
        max_active=1,
        send_timeout_seconds=0.05,
    )

    async def blocked_transport(scope, receive, send):
        async def blocked_send(message):
            if (
                scope["type"] == "http"
                and scope["path"] == "/v1/agent/stream"
                and message["type"] == "http.response.body"
            ):
                await never_send.wait()

            await send(message)

        await app(scope, receive, blocked_send)

    async with AsyncClient(
        transport=ASGITransport(app=blocked_transport),
        base_url="http://test",
    ) as client:
        with pytest.raises(Exception) as captured:
            await asyncio.wait_for(
                client.post(
                    "/v1/agent/stream",
                    json={
                        "prompt": "first",
                        "timeout_seconds": 10,
                    },
                ),
                timeout=2,
            )

        # 确认是发送超时，而不是外层测试保护超时
        leaves = list(
            exception_leaves(captured.value)
        )

        assert leaves, (
            "没有提取到叶子异常："
            f"{captured.value!r}"
        )

        actual_exceptions = "\n".join(
            (
                f"{type(exc).__module__}."
                f"{type(exc).__qualname__}: "
                f"{exc!r}"
            )
            for exc in leaves
        )

        assert all(
            isinstance(exc, SendTimeoutError)
            for exc in leaves
        ), (
            "预期叶子异常全部为 SendTimeoutError，"
            "实际捕获到：\n"
            f"{actual_exceptions}"
        )

        ctx = observed_contexts[0]

        assert (
            ctx.transport_outcome
            is TransportOutcome.SEND_TIMED_OUT
        )
        assert ctx.transport_error
        assert ctx.finalized_event.is_set() is True
        assert ctx.permit_acquired is False
        assert ctx.background_task.done() is True
        assert ctx.deadline_task.done() is True
        assert agent_exited.is_set() is True

        if producer_finishes:
            # 生成已成功，不允许被发送超时覆盖
            assert ctx.outcome is RequestOutcome.SUCCEEDED
            assert ctx.finalize_winner is FinalizeEvent.COMPLETED
            assert ctx.result == "A"
            assert ctx.background_task.cancelled() is False
        else:
            # 生产仍在进行，由发送超时触发取消
            assert ctx.outcome is RequestOutcome.CANCELLED
            assert ctx.finalize_winner is FinalizeEvent.SEND_TIMEOUT
            assert ctx.background_task.cancelled() is True
            assert ctx.cancel_event.is_set() is True

        # 执行名额和活跃请求名额都应恢复
        recovered = await asyncio.wait_for(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "second",
                    "timeout_seconds": 1,
                },
            ),
            timeout=2,
        )

        assert recovered.status_code == 200
        assert recovered.json()["result"] == "recovered"

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "producer_finishes",
    [True, False],
)
async def test_stream_response_deadline_preserves_business_state(
    producer_finishes,
):
    never_finish_agent = asyncio.Event()
    agent_exited = asyncio.Event()
    observed_contexts = []

    body_attempts = 0
    body_completed = 0

    async def fake_stream(prompt, ctx):
        observed_contexts.append(ctx)

        try:
            tokens = (
                "ABCDEF"
                if producer_finishes
                else "A"
            )

            for token in tokens:
                yield token

            if not producer_finishes:
                await never_finish_agent.wait()

        finally:
            agent_exited.set()

    async def fake_agent(prompt, ctx):
        return "recovered"

    app = create_app(
        agent_call=fake_agent,
        agent_stream=fake_stream,
        max_running=1,
        max_waiting=0,
        max_active=1,

        # 单次发送有充足预算。
        send_timeout_seconds=1.0,

        # 整个响应只有 0.2 秒预算。
        response_timeout_seconds=0.2,
    )

    async def slow_transport(scope, receive, send):
        async def slow_send(message):
            nonlocal body_attempts
            nonlocal body_completed

            is_stream_body = (
                scope["type"] == "http"
                and scope["path"] == "/v1/agent/stream"
                and message["type"] == "http.response.body"
            )

            if is_stream_body:
                body_attempts += 1

                # 第一条立即发送，后续持续缓慢发送。
                if body_attempts > 1:
                    await asyncio.sleep(0.08)

            await send(message)

            if is_stream_body:
                body_completed += 1

        await app(scope, receive, slow_send)

    async with AsyncClient(
        transport=ASGITransport(
            app=slow_transport,
        ),
        base_url="http://test",
    ) as client:
        with pytest.raises(Exception) as captured:
            await asyncio.wait_for(
                client.post(
                    "/v1/agent/stream",
                    json={
                        "prompt": "first",

                        # 业务 Deadline 不应先触发。
                        "timeout_seconds": 10,
                    },
                ),
                timeout=3,
            )

        leaves = list(
            exception_leaves(captured.value)
        )

        assert leaves
        assert all(
            isinstance(
                exc,
                ResponseDeadlineExceeded,
            )
            for exc in leaves
        ), [
            f"{type(exc).__name__}: {exc!r}"
            for exc in leaves
        ]

        assert body_completed >= 1

        ctx = observed_contexts[0]

        assert ctx.transport_outcome == (
            TransportOutcome.RESPONSE_TIMED_OUT
        )
        assert ctx.transport_error

        assert ctx.finalized_event.is_set()
        assert ctx.background_task.done()
        assert ctx.deadline_task.done()
        assert ctx.permit_acquired is False
        assert agent_exited.is_set()

        if producer_finishes:
            assert ctx.outcome == (
                RequestOutcome.SUCCEEDED
            )
            assert ctx.finalize_winner == (
                FinalizeEvent.COMPLETED
            )
            assert ctx.result == "ABCDEF"
            assert ctx.error is None
            assert not ctx.background_task.cancelled()

        else:
            assert ctx.outcome == (
                RequestOutcome.TIMED_OUT
            )
            assert ctx.finalize_winner is (
                FinalizeEvent.RESPONSE_DEADLINE_EXCEEDED
            )
            assert ctx.background_task.cancelled()
            assert ctx.cancel_event.is_set()

        # max_active=1：
        # 后续请求成功，验证活跃请求名额已经恢复。
        recovered = await asyncio.wait_for(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "second",
                    "timeout_seconds": 1,
                },
            ),
            timeout=2,
        )

        assert recovered.status_code == 200
        assert recovered.json()["result"] == "recovered"
