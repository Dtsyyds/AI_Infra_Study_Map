import pytest
import asyncio

from month04_deploy.app.metrics import ServiceMetrics

from httpx import ASGITransport, AsyncClient

from month04_deploy.app.api import create_app
from month04_deploy.app.metrics import ServiceMetrics
from month04_deploy.app.request_context import RequestOutcome

@pytest.mark.parametrize(
    "started_at, finished_at, expected_count, expected_sum",
    [
        (None, 0.08, 0, 0.0),   # 排队超时，生产者未启动
        (0.10, 0.55, 1, 0.45),  # 包含取消后的内部清理时间
        (0.0, 0.60, 1, 0.60),   # 0.0 也是合法的开始时间
    ],
)
def test_observe_producer_duration(
    started_at,
    finished_at,
    expected_count,
    expected_sum,
):
    metrics = ServiceMetrics()

    metrics.observe_producer_duration(
        started_at,
        finished_at,
    )

    assert metrics.registry.get_sample_value(
        "agent_producer_duration_seconds_count"
    ) == expected_count

    assert metrics.registry.get_sample_value(
        "agent_producer_duration_seconds_sum"
    ) == pytest.approx(expected_sum)

@pytest.mark.asyncio
async def test_run_request_records_producer_duration():
    metric_name = "agent_producer_duration_seconds_count"

    async def fake_agent(prompt, ctx):
        # Agent 尚未结束，此时不应记录最终耗时。
        assert app.state.metrics.registry.get_sample_value(
            metric_name
        ) == 0

        return "metric-ok"

    app = create_app(agent_call=fake_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/agent/run",
            json={"prompt": "hello", "timeout_seconds": 1},
        )

    assert response.status_code == 200
    assert response.json()["result"] == "metric-ok"

    # 请求完成后，恰好增加一个生产者耗时样本。
    assert app.state.metrics.registry.get_sample_value(
        metric_name
    ) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected_status, expected_outcome",
    [
        ("error", 500, "failed"),
        ("timeout", 504, "timed_out"),
    ],
)
async def test_run_failure_records_producer_duration(
    mode,
    expected_status,
    expected_outcome,
):
    metric_name = "agent_producer_duration_seconds_count"
    cleanup_finished = asyncio.Event()

    async def fake_agent(prompt, ctx):
        try:
            if mode == "error":
                raise RuntimeError("test agent failure")

            # 持续等待，让业务 Deadline 触发取消。
            await asyncio.Event().wait()

        finally:
            # 模拟内部清理中存在异步等待。
            await asyncio.sleep(0)

            # Agent 的内部清理尚未退出，不能提前采样。
            assert app.state.metrics.registry.get_sample_value(
                metric_name
            ) == 0

            cleanup_finished.set()

    app = create_app(agent_call=fake_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/v1/agent/run",
                json={
                    "prompt": "hello",
                    "timeout_seconds": 0.2,
                },
            ),
            timeout=3,
        )

    assert response.status_code == expected_status
    assert response.json()["outcome"] == expected_outcome
    assert cleanup_finished.is_set()

    # 失败或取消的生产者，也应当恰好记录一次耗时。
    assert app.state.metrics.registry.get_sample_value(
        metric_name
    ) == 1

@pytest.mark.asyncio
async def test_stream_request_records_one_producer_sample():
    metric_name = "agent_producer_duration_seconds_count"
    cleanup_finished = asyncio.Event()

    async def fake_stream(prompt, ctx):
        try:
            for token in ("A", "B", "C"):
                yield token
        finally:
            await asyncio.sleep(0)

            # 原始生成器还在清理，不能提前记录最终耗时。
            assert app.state.metrics.registry.get_sample_value(
                metric_name
            ) == 0

            cleanup_finished.set()

    app = create_app(agent_stream=fake_stream)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/v1/agent/stream",
                json={"prompt": "hello", "timeout_seconds": 1},
            ),
            timeout=3,
        )

    assert response.status_code == 200
    assert cleanup_finished.is_set()

    # 三个 Token 对应一个生产者耗时样本。
    assert app.state.metrics.registry.get_sample_value(
        metric_name
    ) == 1

@pytest.mark.asyncio
async def test_stream_backpressure_cancel_records_after_cleanup():
    metric_name = "agent_producer_duration_seconds_count"

    send_started = asyncio.Event()
    last_token_ready = asyncio.Event()
    allow_send = asyncio.Event()

    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    contexts = []

    async def fake_stream(prompt, ctx):
        contexts.append(ctx)

        # 一个 Token 被发送端取走后，仍足以填满队列，
        # 并让最后一个 Token 阻塞在 queue.put()。
        token_count = ctx.stream_queue.maxsize + 2

        try:
            for index in range(token_count):
                if index == token_count - 1:
                    last_token_ready.set()

                yield str(index)

        finally:
            cleanup_started.set()
            await allow_cleanup.wait()
            cleanup_finished.set()

    app = create_app(
        agent_stream=fake_stream,
        max_running=1,
        max_waiting=0,
        max_active=1,
        send_timeout_seconds=30,
        response_timeout_seconds=30,
    )

    def sample_count():
        return app.state.metrics.registry.get_sample_value(
            metric_name
        )

    async def blocked_transport(scope, receive, send):
        async def blocked_send(message):
            if message["type"] == "http.response.body":
                send_started.set()
                await allow_send.wait()

            await send(message)

        await app(scope, receive, blocked_send)

    async with AsyncClient(
        transport=ASGITransport(app=blocked_transport),
        base_url="http://test",
    ) as client:
        request_task = asyncio.create_task(
            client.post(
                "/v1/agent/stream",
                json={
                    "prompt": "hello",
                    "timeout_seconds": 30,
                },
            )
        )

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    send_started.wait(),
                    last_token_ready.wait(),
                ),
                timeout=2,
            )

            ctx = contexts[0]

            # 观察点一：队列满，生产者还没结束。
            assert ctx.stream_queue.full()
            assert not cleanup_started.is_set()
            assert sample_count() == 0
            assert ctx.permit_acquired is True

            request_task.cancel()

            await asyncio.wait_for(
                cleanup_started.wait(),
                timeout=2,
            )

            # 观察点二：取消已触发，内部清理仍在等待。
            assert not cleanup_finished.is_set()
            assert sample_count() == 0
            assert ctx.permit_acquired is True

            allow_cleanup.set()

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request_task, timeout=2)

            # 观察点三：生产者清理完成，请求已经退出。
            assert cleanup_finished.is_set()
            assert ctx.background_task.done()
            assert ctx.background_task.cancelled()
            assert sample_count() == 1
            assert ctx.permit_acquired is False

        finally:
            # 即使断言失败，也放行等待并回收请求任务。
            allow_cleanup.set()
            allow_send.set()

            if not request_task.done():
                request_task.cancel()

            await asyncio.wait_for(
                asyncio.gather(
                    request_task,
                    return_exceptions=True,
                ),
                timeout=2,
            )

@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_producer_histogram():
    metric_name = (
        "agent_producer_duration_seconds_count"
    )

    async def fake_agent(prompt, ctx):
        return "metric-ok"

    app = create_app(agent_call=fake_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        before = await client.get("/metrics")

        run_response = await client.post(
            "/v1/agent/run",
            json={
                "prompt": "hello",
                "timeout_seconds": 1,
            },
        )

        after = await client.get("/metrics")

    assert before.status_code == 200
    assert run_response.status_code == 200
    assert after.status_code == 200

    assert before.headers["content-type"].startswith(
        "text/plain"
    )

    assert (
        f"{metric_name} 0.0"
        in before.text
    )
    assert (
        f"{metric_name} 1.0"
        in after.text
    )

@pytest.mark.parametrize(
    "result",
    [
        "acquired",
        "cancelled",
        "error",
    ],
)
def test_observe_queue_wait_records_result_label(result):
    metrics = ServiceMetrics()

    metrics.observe_queue_wait(
        started_at=0.10,
        finished_at=0.50,
        result=result,
    )

    labels = {"result": result}

    assert metrics.registry.get_sample_value(
        "agent_queue_wait_seconds_count",
        labels,
    ) == 1

    assert metrics.registry.get_sample_value(
        "agent_queue_wait_seconds_sum",
        labels,
    ) == pytest.approx(0.40)

@pytest.mark.asyncio
async def test_queue_wait_records_acquired_after_waiting():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def fake_agent(prompt, ctx):
        if prompt == "first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()

        return prompt

    app = create_app(
        agent_call=fake_agent,
        max_running=1,
        max_waiting=1,
        max_active=2,
    )

    def queue_metric(
        suffix: str,
        result: str,
    ):
        return app.state.metrics.registry.get_sample_value(
            f"agent_queue_wait_seconds_{suffix}",
            {"result": result},
        )

    first_task = None
    second_task = None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        try:
            first_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "first",
                        "timeout_seconds": 1,
                    },
                )
            )

            await asyncio.wait_for(
                first_started.wait(),
                timeout=1,
            )

            # 第一个请求立即获得执行名额，
            # 因此已经产生一个 acquired 样本。
            assert queue_metric(
                "count",
                "acquired",
            ) == 1

            second_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 1,
                    },
                )
            )

            # 给第二个请求时间进入执行名额等待。
            await asyncio.sleep(0.05)

            # 第一个请求仍占用执行名额，
            # 第二个请求不能开始运行。
            assert not second_started.is_set()

            # B 仍在排队，排队阶段尚未结束，
            # 所以暂时没有产生第二个样本。
            assert queue_metric(
                "count",
                "acquired",
            ) == 1

            release_first.set()

            first_response, second_response = (
                await asyncio.wait_for(
                    asyncio.gather(
                        first_task,
                        second_task,
                    ),
                    timeout=2,
                )
            )

            assert first_response.status_code == 200
            assert second_response.status_code == 200
            assert second_started.is_set()

            # A 和 B 最终都获得过执行名额。
            assert queue_metric(
                "count",
                "acquired",
            ) == 2

            # A 的等待时间接近零，B 至少等待了约 0.05 秒。
            assert queue_metric(
                "sum",
                "acquired",
            ) >= 0.04

        finally:
            release_first.set()

            tasks = [
                task
                for task in (
                    first_task,
                    second_task,
                )
                if task is not None
            ]

            if tasks:
                await asyncio.wait_for(
                    asyncio.gather(
                        *tasks,
                        return_exceptions=True,
                    ),
                    timeout=2,
                )

@pytest.mark.asyncio
async def test_queue_wait_records_cancelled_on_deadline():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    called_prompts = []

    async def fake_agent(prompt, ctx):
        called_prompts.append(prompt)

        if prompt == "first":
            first_started.set()
            await release_first.wait()

        return prompt

    app = create_app(
        agent_call=fake_agent,
        max_running=1,
        max_waiting=1,
        max_active=2,
    )

    def queue_metric(
        suffix: str,
        result: str,
    ):
        return app.state.metrics.registry.get_sample_value(
            f"agent_queue_wait_seconds_{suffix}",
            {"result": result},
        )

    def producer_count():
        return app.state.metrics.registry.get_sample_value(
            "agent_producer_duration_seconds_count"
        )

    first_task = None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        try:
            first_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "first",
                        "timeout_seconds": 2,
                    },
                )
            )

            await asyncio.wait_for(
                first_started.wait(),
                timeout=1,
            )

            # A 已经获得执行名额。
            assert queue_metric(
                "count",
                "acquired",
            ) == 1

            second_response = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 0.05,
                    },
                ),
                timeout=1,
            )

            assert second_response.status_code == 504
            assert (
                second_response.json()["outcome"]
                == "timed_out"
            )

            # B 从未进入 Agent。
            assert called_prompts == ["first"]

            # A 成功获得名额，B 在等待时被取消。
            assert queue_metric(
                "count",
                "acquired",
            ) == 1

            assert queue_metric(
                "count",
                "cancelled",
            ) == 1

            assert queue_metric(
                "sum",
                "cancelled",
            ) > 0.0

            # A 仍未退出；B 从未启动生产者，
            # 因此当前还没有生产者耗时样本。
            assert producer_count() == 0

            release_first.set()

            first_response = await asyncio.wait_for(
                first_task,
                timeout=1,
            )

            assert first_response.status_code == 200

            # 只有真正启动过的 A 产生生产者样本。
            assert producer_count() == 1

        finally:
            release_first.set()

            if (
                first_task is not None
                and not first_task.done()
            ):
                first_task.cancel()

            if first_task is not None:
                await asyncio.wait_for(
                    asyncio.gather(
                        first_task,
                        return_exceptions=True,
                    ),
                    timeout=1,
                )

def test_request_state_gauges_track_current_values():
    metrics = ServiceMetrics()

    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 0
    assert metrics.registry.get_sample_value(
        "agent_waiting_requests"
    ) == 0

    metrics.execution_acquired()
    metrics.queue_entered()

    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 1
    assert metrics.registry.get_sample_value(
        "agent_waiting_requests"
    ) == 1

    metrics.queue_left()
    metrics.execution_released()

    assert metrics.registry.get_sample_value(
        "agent_running_requests"
    ) == 0
    assert metrics.registry.get_sample_value(
        "agent_waiting_requests"
    ) == 0

@pytest.mark.asyncio
async def test_active_running_and_waiting_gauges_follow_request_lifecycle():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    called_prompts: list[str] = []

    async def fake_agent(prompt, ctx):
        called_prompts.append(prompt)

        if prompt == "first":
            first_started.set()
            await release_first.wait()

        return prompt

    app = create_app(
        agent_call=fake_agent,
        max_running=1,
        max_waiting=1,
        max_active=2,
    )

    def gauge(name: str) -> float:
        value = app.state.metrics.registry.get_sample_value(name)

        if value is None:
            raise AssertionError(f"metric does not exist: {name}")

        return value

    async def wait_for_gauge(
        name: str,
        expected: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1

        while loop.time() < deadline:
            if gauge(name) == expected:
                return

            await asyncio.sleep(0.01)

        raise AssertionError(
            f"{name} did not become {expected}; "
            f"current={gauge(name)}"
        )

    first_task: asyncio.Task | None = None
    second_task: asyncio.Task | None = None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        try:
            # 初始状态：没有活跃、运行或等待请求。
            assert gauge("agent_active_requests") == 0
            assert gauge("agent_running_requests") == 0
            assert gauge("agent_waiting_requests") == 0
            assert gauge(
                "agent_http_request_duration_seconds_count"
            ) == 0

            # A 获得 active slot 和 execution permit。
            first_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "first",
                        "timeout_seconds": 2,
                    },
                )
            )

            await asyncio.wait_for(
                first_started.wait(),
                timeout=1,
            )

            assert gauge("agent_active_requests") == 1
            assert gauge("agent_running_requests") == 1
            assert gauge("agent_waiting_requests") == 0
            assert called_prompts == ["first"]

            # B 获得第二个 active slot，但执行名额已被 A 占用，
            # 因此 B 进入等待队列。
            second_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 0.2,
                    },
                )
            )

            await wait_for_gauge(
                "agent_waiting_requests",
                1,
            )

            assert gauge("agent_active_requests") == 2
            assert gauge("agent_running_requests") == 1
            assert gauge("agent_waiting_requests") == 1

            # B 尚未获得执行名额，因此 fake_agent 没有收到 second。
            assert called_prompts == ["first"]

            # active 容量已经被 A、B 占满。
            # C 应在中间件层直接被拒绝，不进入排队或执行阶段。
            third_response = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "third",
                        "timeout_seconds": 1,
                    },
                ),
                timeout=1,
            )

            assert gauge(
                "agent_http_request_duration_seconds_count"
            ) == 1

            assert third_response.status_code == 503
            assert third_response.json()["detail"]["code"] == (
                "active_request_limit"
            )

            # C 没有获得 active slot，因此三个 Gauge 都不变化。
            assert gauge("agent_active_requests") == 2
            assert gauge("agent_running_requests") == 1
            assert gauge("agent_waiting_requests") == 1
            assert called_prompts == ["first"]

            # B 等待执行名额期间到达业务截止时间。
            second_response = await asyncio.wait_for(
                second_task,
                timeout=1,
            )

            assert gauge(
                "agent_http_request_duration_seconds_count"
            ) == 2

            assert second_response.status_code == 504
            assert second_response.json()["outcome"] == "timed_out"

            # B 已经退出 HTTP 请求和等待队列。
            # A 仍在运行。
            assert gauge("agent_active_requests") == 1
            assert gauge("agent_running_requests") == 1
            assert gauge("agent_waiting_requests") == 0
            assert called_prompts == ["first"]

            # 允许 A 正常结束。
            release_first.set()

            first_response = await asyncio.wait_for(
                first_task,
                timeout=1,
            )

            assert first_response.status_code == 200
            assert first_response.json()["outcome"] == "succeeded"

            assert gauge(
                "agent_http_request_duration_seconds_count"
            ) == 3

            assert gauge(
                "agent_http_request_duration_seconds_sum"
            ) > 0

            # A 的执行名额和 active slot 都已经释放。
            assert gauge("agent_active_requests") == 0
            assert gauge("agent_running_requests") == 0
            assert gauge("agent_waiting_requests") == 0
            assert called_prompts == ["first"]

        finally:
            # finally 只做兜底清理，不放业务断言，
            # 避免覆盖真正的测试失败信息。
            release_first.set()

            tasks = [
                task
                for task in (first_task, second_task)
                if task is not None
            ]

            for task in tasks:
                if not task.done():
                    task.cancel()

            if tasks:
                await asyncio.wait_for(
                    asyncio.gather(
                        *tasks,
                        return_exceptions=True,
                    ),
                    timeout=1,
                )

def test_http_request_duration_records_sample():
    metrics = ServiceMetrics()

    metrics.observe_http_request_duration(
        started_at=10.0,
        finished_at=10.4,
    )

    count = metrics.registry.get_sample_value(
        "agent_http_request_duration_seconds_count"
    )
    total = metrics.registry.get_sample_value(
        "agent_http_request_duration_seconds_sum"
    )

    assert count == 1
    assert total == pytest.approx(0.4)

def test_request_outcome_counter_records_all_outcomes():
    metrics = ServiceMetrics()

    for outcome in RequestOutcome:
        metrics.record_request_outcome(outcome)

    for outcome in RequestOutcome:
        value = metrics.registry.get_sample_value(
            "agent_request_outcomes_total",
            {"outcome": outcome.value},
        )

        assert value == 1

def test_admission_rejection_counter_records_reasons():
    metrics = ServiceMetrics()

    metrics.record_admission_rejection(
        "active_limit"
    )
    metrics.record_admission_rejection(
        "capacity_exceeded"
    )

    active_limit = metrics.registry.get_sample_value(
        "agent_admission_rejections_total",
        {"reason": "active_limit"},
    )
    capacity_exceeded = metrics.registry.get_sample_value(
        "agent_admission_rejections_total",
        {"reason": "capacity_exceeded"},
    )

    assert active_limit == 1
    assert capacity_exceeded == 1

@pytest.mark.asyncio
async def test_capacity_rejection_counter():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    called_prompts: list[str] = []

    async def fake_agent(prompt, ctx):
        called_prompts.append(prompt)

        if prompt == "first":
            first_started.set()
            await release_first.wait()

        return prompt

    app = create_app(
        agent_call=fake_agent,
        max_running=1,
        max_waiting=1,
        # active 容量必须大于运行容量加排队容量，
        # 否则第三个请求会先被 active-limit 拒绝。
        max_active=3,
    )

    def sample(
        name: str,
        labels: dict[str, str] | None = None,
    ) -> float:
        value = app.state.metrics.registry.get_sample_value(
            name,
            labels,
        )

        if value is None:
            raise AssertionError(
                f"missing metric: {name}, labels={labels}"
            )

        return value

    first_task: asyncio.Task | None = None
    second_task: asyncio.Task | None = None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        try:
            # A 占用唯一执行名额。
            first_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "first",
                        "timeout_seconds": 2,
                    },
                )
            )

            await asyncio.wait_for(
                first_started.wait(),
                timeout=1,
            )

            # B 占用唯一等待槽位。
            second_task = asyncio.create_task(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "second",
                        "timeout_seconds": 2,
                    },
                )
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 1

            while loop.time() < deadline:
                if sample("agent_waiting_requests") == 1:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(
                    "second request did not enter queue"
                )

            assert sample("agent_active_requests") == 2
            assert sample("agent_running_requests") == 1
            assert sample("agent_waiting_requests") == 1
            assert called_prompts == ["first"]

            # C 可以进入 active 中间件，但控制器容量已满。
            third_response = await asyncio.wait_for(
                client.post(
                    "/v1/agent/run",
                    json={
                        "prompt": "third",
                        "timeout_seconds": 1,
                    },
                ),
                timeout=1,
            )

            assert third_response.status_code == 503
            assert third_response.json()["detail"]["code"] == (
                "capacity_exceeded"
            )

            assert sample(
                "agent_admission_rejections_total",
                {"reason": "active_limit"},
            ) == 0

            assert sample(
                "agent_admission_rejections_total",
                {"reason": "capacity_exceeded"},
            ) == 1

            # C 没有进入 Agent 生产者。
            assert called_prompts == ["first"]

            # C 的 HTTP 请求已经退出，因此 active 恢复为 A+B。
            assert sample("agent_active_requests") == 2
            assert sample("agent_running_requests") == 1
            assert sample("agent_waiting_requests") == 1

            release_first.set()

            first_response, second_response = await asyncio.wait_for(
                asyncio.gather(
                    first_task,
                    second_task,
                ),
                timeout=1,
            )

            assert first_response.status_code == 200
            assert second_response.status_code == 200
            assert called_prompts == ["first", "second"]

            assert sample("agent_active_requests") == 0
            assert sample("agent_running_requests") == 0
            assert sample("agent_waiting_requests") == 0

        finally:
            release_first.set()

            tasks = [
                task
                for task in (first_task, second_task)
                if task is not None
            ]

            for task in tasks:
                if not task.done():
                    task.cancel()

            if tasks:
                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )