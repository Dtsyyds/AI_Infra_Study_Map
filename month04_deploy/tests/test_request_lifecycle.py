import asyncio

import pytest

import json

from month04_deploy.app.request_context import (
    FinalizeEvent,
    RequestContext,
    RequestOutcome,
    RequestPhase,
)
from month04_deploy.app.request_lifecycle import try_claim_finalize, finalize_request, cleanup_request_resources
from month04_deploy.app.request_runner import start_agent_request
from month04_deploy.app.deadline_monitor import (
    start_deadline_monitor,
)
from month04_deploy.app.client_disconnect import (
    start_client_disconnect_monitor,
)

from month04_deploy.app.stream_runner import (
    start_streaming_agent_request,
)

from month04_deploy.app.stream_consumer import (
    iter_stream_jsonl
)

from month04_deploy.app.admission import AdmissionController
from month04_deploy.app.metrics import ServiceMetrics

@pytest.mark.asyncio
async def test_completed_claims_success():
    loop = asyncio.get_running_loop()
    ctx = RequestContext(
        request_id="req-001",
        deadline=loop.time() + 10,
    )

    claimed = await try_claim_finalize(
        ctx,
        FinalizeEvent.COMPLETED,
    )

    assert claimed is True
    assert ctx.phase is RequestPhase.FINALIZING
    assert ctx.outcome is RequestOutcome.SUCCEEDED
    assert ctx.finalize_winner is FinalizeEvent.COMPLETED

    # 当前只是选出终结者，清理尚未完成
    assert ctx.finalized_event.is_set() is False

@pytest.mark.asyncio
async def test_second_event_cannot_claim():
    """完成事件获得终结权后，断开事件必须失败。"""
    loop = asyncio.get_running_loop()
    ctx = RequestContext(
        request_id="req-002",
        deadline=loop.time() + 10,
    )
    first_claimed = await try_claim_finalize(
        ctx,
        FinalizeEvent.COMPLETED,
    )
    second_claimed = await try_claim_finalize(
        ctx,
        FinalizeEvent.CLIENT_DISCONNECTED,
    )

    assert first_claimed is True
    assert second_claimed is False

    # 第二个事件不能覆盖第一个终结者
    assert ctx.phase is RequestPhase.FINALIZING
    assert ctx.finalize_winner is FinalizeEvent.COMPLETED
    assert ctx.outcome is RequestOutcome.SUCCEEDED
    assert ctx.finalized_event.is_set() is False

@pytest.mark.asyncio
async def test_completed_after_deadline_becomes_timeout():
    """完成回调迟于Deadline时，结果必须是TIMED_OUT。"""
    loop = asyncio.get_running_loop()
    ctx = RequestContext(
        request_id="req-003",
        deadline=loop.time() - 0.001,
    )

    claimed = await try_claim_finalize(
        ctx,
        FinalizeEvent.COMPLETED,
    )

    # 获得终结权不等于执行成功
    assert claimed is True
    assert ctx.phase is RequestPhase.FINALIZING
    assert ctx.finalize_winner is FinalizeEvent.DEADLINE_EXCEEDED
    assert ctx.outcome is RequestOutcome.TIMED_OUT
    assert ctx.finalized_event.is_set() is False

@pytest.mark.asyncio
async def test_concurrent_events_have_exactly_one_winner():
    """
    使用asyncio.Event作为起跑门，
    让COMPLETED、CLIENT_DISCONNECTED、INTERNAL_ERROR并发竞争。
    断言返回值中只有一个True。
    """
    loop = asyncio.get_running_loop()
    ctx = RequestContext(
        request_id="req-004",
        deadline=loop.time() + 10,
    )
    events = [
        FinalizeEvent.COMPLETED,
        FinalizeEvent.CLIENT_DISCONNECTED,
        FinalizeEvent.INTERNAL_ERROR,
    ]
    gate = asyncio.Event()

    async def contender(event: FinalizeEvent) -> bool:
        await gate.wait()
        return await try_claim_finalize(ctx, event)

    tasks = [
        asyncio.create_task(contender(event))
        for event in events
    ]

    gate.set()
    results = await asyncio.gather(*tasks)

    assert results.count(True) == 1
    assert ctx.phase is RequestPhase.FINALIZING
    assert ctx.finalize_winner in events
    assert ctx.finalized_event.is_set() is False

@pytest.mark.asyncio
async def test_loser_waits_until_winner_finishes_cleanup():
    loop = asyncio.get_running_loop()
    ctx = RequestContext(
        request_id="req-005",
        deadline=loop.time() + 10,
    )

    cleanup_started = asyncio.Event()
    allow_cleanup_finish = asyncio.Event()
    cleanup_count = 0

    async def cleanup(_: RequestContext) -> None:
        nonlocal cleanup_count
        cleanup_count += 1
        cleanup_started.set()
        await allow_cleanup_finish.wait()

    winner_task = asyncio.create_task(
        finalize_request(
            ctx,
            FinalizeEvent.COMPLETED,
            cleanup,
        )
    )

    # 确保第一个调用者已经进入清理阶段
    await cleanup_started.wait()

    loser_task = asyncio.create_task(
        finalize_request(
            ctx,
            FinalizeEvent.CLIENT_DISCONNECTED,
            cleanup,
        )
    )

    # 给loser一次运行机会
    await asyncio.sleep(0)

    assert ctx.phase is RequestPhase.FINALIZING
    assert loser_task.done() is False
    assert cleanup_count == 1

    # 允许唯一终结者完成清理
    allow_cleanup_finish.set()

    winner_result, loser_result = await asyncio.gather(
        winner_task,
        loser_task,
    )

    assert winner_result is True
    assert loser_result is False
    assert cleanup_count == 1
    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_cancelled_request_stops_task_and_releases_resources():
    loop = asyncio.get_running_loop()
    metrics = ServiceMetrics()

    controller = AdmissionController(
        max_running=1,
        max_waiting=1,
        metrics=metrics,
    )

    ctx = RequestContext(
        request_id="req-006",
        deadline=loop.time() + 10,
    )

    # 必须通过真实准入路径获得容量。
    admitted = await controller.try_admit(ctx)

    assert admitted is True
    assert ctx.permit_acquired is True
    assert ctx.queue_slot_acquired is False
    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 1

    ctx.background_task = asyncio.create_task(
        asyncio.sleep(10)
    )

    finalized_by_me = await finalize_request(
        ctx,
        FinalizeEvent.CLIENT_DISCONNECTED,
        cleanup_request_resources,
    )

    assert finalized_by_me is True
    assert ctx.cancel_event.is_set() is True
    assert ctx.background_task.done()
    assert ctx.background_task.cancelled()

    # 生产者真正退出以后，容量和 Gauge 才归零。
    assert ctx.queue_slot_acquired is False
    assert ctx.permit_acquired is False

    assert metrics.registry.get_sample_value(
        "agent_waiting_requests"
    ) == 0
    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 0

    # 不读取 Semaphore 的私有 _value，
    # 而是通过“下一个请求能否获得名额”验证资源已经释放。
    next_ctx = RequestContext(
        request_id="req-007",
        deadline=loop.time() + 10,
    )

    next_admitted = await controller.try_admit(next_ctx)

    assert next_admitted is True
    assert next_ctx.permit_acquired is True

    controller.release_capacity(next_ctx)

    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 0

@pytest.mark.asyncio
async def test_external_disconnect_cancels_running_agent():
    loop = asyncio.get_running_loop()
    agent_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def fake_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        agent_started.set()
        await never_finish.wait()
        return "不应该返回"

    ctx = RequestContext(
        request_id="req-007",
        deadline=loop.time() + 10,
    )

    task = start_agent_request(
        ctx,
        "hello",
        fake_agent,
    )

    # 确保Agent确实进入运行状态
    await agent_started.wait()

    claimed = await finalize_request(
        ctx,
        FinalizeEvent.CLIENT_DISCONNECTED,
        cleanup_request_resources,
    )

    assert claimed is True
    assert task.cancelled() is True
    assert ctx.outcome is RequestOutcome.CANCELLED
    assert ctx.cancel_event.is_set() is True
    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True
    assert ctx.finalize_winner is FinalizeEvent.CLIENT_DISCONNECTED
    assert ctx.result is None
    assert ctx.error is None

@pytest.mark.asyncio
async def test_agent_completion_publishes_success():
    loop = asyncio.get_running_loop()

    async def fake_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        await asyncio.sleep(0)
        return f"answer:{prompt}"

    ctx = RequestContext(
        request_id="req-008",
        deadline=loop.time() + 10,
    )

    task = start_agent_request(
        ctx,
        "hello",
        fake_agent,
    )

    await task

    assert task.done() is True
    assert task.cancelled() is False
    assert ctx.background_task is task

    assert ctx.result == "answer:hello"
    assert ctx.error is None

    assert ctx.finalize_winner is FinalizeEvent.COMPLETED
    assert ctx.outcome is RequestOutcome.SUCCEEDED

    # 成功请求不需要传播取消
    assert ctx.cancel_event.is_set() is False

    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_agent_error_publishes_failed():
    loop = asyncio.get_running_loop()

    async def failing_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        await asyncio.sleep(0)
        raise RuntimeError("fake agent failed")

    ctx = RequestContext(
        request_id="req-009",
        deadline=loop.time() + 10,
    )

    task = start_agent_request(
        ctx,
        "hello",
        failing_agent,
    )

    # run_agent_request会捕获业务异常，因此这里不会再次抛出
    await task

    assert task.done() is True
    assert task.cancelled() is False

    assert ctx.result is None
    assert ctx.error == "fake agent failed"

    assert ctx.finalize_winner is FinalizeEvent.INTERNAL_ERROR
    assert ctx.outcome is RequestOutcome.FAILED

    assert ctx.cancel_event.is_set() is True
    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_deadline_cancels_running_agent():
    loop = asyncio.get_running_loop()
    agent_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        agent_started.set()
        await never_finish.wait()
        return "不应该返回"

    ctx = RequestContext(
        request_id="req-010",
        deadline=loop.time() + 0.02,
    )

    agent_task = start_agent_request(
        ctx,
        "slow request",
        slow_agent,
    )
    deadline_task = start_deadline_monitor(ctx)

    await agent_started.wait()

    deadline_won = await deadline_task

    assert deadline_won is True
    assert ctx.deadline_task is deadline_task

    assert agent_task.cancelled() is True
    assert ctx.finalize_winner is FinalizeEvent.DEADLINE_EXCEEDED
    assert ctx.outcome is RequestOutcome.TIMED_OUT
    assert ctx.cancel_event.is_set() is True

    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_success_before_deadline_stops_monitor():
    loop = asyncio.get_running_loop()

    async def fast_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        await asyncio.sleep(0)
        return "fast answer"

    ctx = RequestContext(
        request_id="req-011",
        deadline=loop.time() + 1,
    )

    deadline_task = start_deadline_monitor(ctx)
    agent_task = start_agent_request(
        ctx,
        "fast request",
        fast_agent,
    )

    await agent_task
    deadline_won = await deadline_task

    assert deadline_won is False
    assert deadline_task.done() is True

    assert ctx.result == "fast answer"
    assert ctx.finalize_winner is FinalizeEvent.COMPLETED
    assert ctx.outcome is RequestOutcome.SUCCEEDED
    assert ctx.cancel_event.is_set() is False

    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_client_disconnect_cancels_running_agent():
    loop = asyncio.get_running_loop()
    agent_started = asyncio.Event()
    never_finish = asyncio.Event()

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    async def slow_agent(
        prompt: str,
        ctx: RequestContext,
    ) -> str:
        agent_started.set()
        await never_finish.wait()
        return "不应该返回"

    ctx = RequestContext(
        request_id="req-012",
        deadline=loop.time() + 10,
    )

    agent_task = start_agent_request(
        ctx,
        "slow request",
        slow_agent,
    )

    await agent_started.wait()

    disconnect_task = start_client_disconnect_monitor(
        DisconnectedRequest(),
        ctx,
        poll_interval=0.001,
    )

    disconnect_won = await disconnect_task

    assert disconnect_won is True
    assert ctx.disconnect_task is disconnect_task

    assert agent_task.cancelled() is True
    assert ctx.finalize_winner is FinalizeEvent.CLIENT_DISCONNECTED
    assert ctx.outcome is RequestOutcome.CANCELLED
    assert ctx.cancel_event.is_set() is True

    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.finalized_event.is_set() is True

@pytest.mark.asyncio
async def test_bounded_stream_buffer_applies_backpressure():
    loop = asyncio.get_running_loop()
    second_token_ready = asyncio.Event()

    async def fake_streaming_agent(
        prompt: str,
        ctx: RequestContext,
    ):
        yield "A"

        second_token_ready.set()
        yield "B"

    ctx = RequestContext(
        request_id="req-013",
        deadline=loop.time() + 10,
        stream_queue=asyncio.Queue(maxsize=1),
    )

    producer_task = start_streaming_agent_request(
        ctx,
        "hello",
        fake_streaming_agent,
    )

    # Agent已经产生第二个Token
    await second_token_ready.wait()

    # 让生产者执行到第二次queue.put()
    await asyncio.sleep(0)

    assert ctx.stream_queue.qsize() == 1
    assert producer_task.done() is False

    first_token = await ctx.stream_queue.get()
    ctx.stream_queue.task_done()

    assert first_token == "A"

    # 消费一个Token后，生产者才能放入B并完成
    await asyncio.wait_for(producer_task, timeout=1)

    second_token = await ctx.stream_queue.get()
    ctx.stream_queue.task_done()

    assert second_token == "B"
    assert ctx.result == "AB"

    assert ctx.finalize_winner is FinalizeEvent.COMPLETED
    assert ctx.outcome is RequestOutcome.SUCCEEDED
    assert ctx.phase is RequestPhase.FINALIZED

@pytest.mark.asyncio
async def test_stream_consumer_drains_tokens_then_emits_done():
    loop = asyncio.get_running_loop()

    async def fake_streaming_agent(
            prompt: str,
            ctx: RequestContext,
    ):
        yield "A"
        await asyncio.sleep(0)
        yield "B"

    ctx = RequestContext(
        request_id="req-014",
        deadline=loop.time() + 10,
        stream_queue=asyncio.Queue(maxsize=1),
    )

    producer_task = start_streaming_agent_request(
        ctx,
        "hello",
        fake_streaming_agent,
    )

    events = [
        json.loads(chunk)
        async for chunk in iter_stream_jsonl(ctx)
    ]

    await producer_task

    assert events == [
        {
            "type": "token",
            "data": "A",
        },
        {
            "type": "token",
            "data": "B",
        },
        {
            "type": "done",
            "request_id": "req-014",
            "outcome": "succeeded",
            "error": None,
        },
    ]

    assert ctx.result == "AB"
    assert ctx.outcome is RequestOutcome.SUCCEEDED
    assert ctx.phase is RequestPhase.FINALIZED
    assert ctx.stream_queue.empty() is True

@pytest.mark.asyncio
async def test_disconnect_cancels_producer_blocked_on_full_queue():
    loop = asyncio.get_running_loop()
    second_token_ready = asyncio.Event()
    reached_third_token = asyncio.Event()

    async def fake_stream(prompt, ctx):
        yield "A"

        second_token_ready.set()
        yield "B"

        # 只有B成功入队，生产者才会继续请求下一个Token
        reached_third_token.set()
        yield "C"

    ctx = RequestContext(
        request_id="req-full-buffer",
        deadline=loop.time() + 10,
        stream_queue=asyncio.Queue(maxsize=1),
    )

    producer_task = start_streaming_agent_request(
        ctx,
        "hello",
        fake_stream,
    )

    try:
        await asyncio.wait_for(
            second_token_ready.wait(),
            timeout=1,
        )

        # 没有消费者：A占满队列，B等待入队
        assert ctx.stream_queue.full() is True
        assert producer_task.done() is False
        assert reached_third_token.is_set() is False

        claimed = await asyncio.wait_for(
            finalize_request(
                ctx,
                FinalizeEvent.CLIENT_DISCONNECTED,
                cleanup_request_resources,
            ),
            timeout=1,
        )

        assert claimed is True
        assert producer_task.cancelled() is True
        assert reached_third_token.is_set() is False

        assert ctx.finalize_winner is FinalizeEvent.CLIENT_DISCONNECTED
        assert ctx.outcome is RequestOutcome.CANCELLED
        assert ctx.cancel_event.is_set() is True
        assert ctx.phase is RequestPhase.FINALIZED
        assert ctx.finalized_event.is_set() is True

    finally:
        # 即使断言失败，也回收测试创建的生产者
        if not producer_task.done():
            producer_task.cancel()

        await asyncio.gather(
            producer_task,
            return_exceptions=True,
        )

@pytest.mark.asyncio
async def test_outcome_recorded_only_after_cleanup_finishes():
    loop = asyncio.get_running_loop()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    metrics = ServiceMetrics()

    ctx = RequestContext(
        request_id="outcome-timing",
        deadline=loop.time() + 10,
        outcome_recorder=metrics.record_request_outcome,
    )

    async def blocking_cleanup(
        cleanup_ctx: RequestContext,
    ) -> None:
        cleanup_started.set()
        await allow_cleanup.wait()

    finalize_task = asyncio.create_task(
        finalize_request(
            ctx,
            FinalizeEvent.CLIENT_DISCONNECTED,
            blocking_cleanup,
        )
    )

    try:
        await asyncio.wait_for(
            cleanup_started.wait(),
            timeout=1,
        )

        # outcome 已经确定，但请求仍处于 FINALIZING。
        assert ctx.outcome is RequestOutcome.CANCELLED
        assert ctx.phase is RequestPhase.FINALIZING

        # 清理没有完成，因此不能提前累计。
        assert metrics.registry.get_sample_value(
            "agent_request_outcomes_total",
            {"outcome": "cancelled"},
        ) == 0

        allow_cleanup.set()

        finalized_by_me = await asyncio.wait_for(
            finalize_task,
            timeout=1,
        )

        assert finalized_by_me is True
        assert ctx.phase is RequestPhase.FINALIZED

        assert metrics.registry.get_sample_value(
            "agent_request_outcomes_total",
            {"outcome": "cancelled"},
        ) == 1

    finally:
        allow_cleanup.set()

        if not finalize_task.done():
            finalize_task.cancel()

        await asyncio.gather(
            finalize_task,
            return_exceptions=True,
        )