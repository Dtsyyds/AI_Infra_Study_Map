from month03_llm_serving.request_queue import InferenceRequest
from month03_llm_serving.scheduler import (
    BatchScheduler,
    RequestKVCacheTooLargeError,
    RequestPhase,
    RequestState,
)

import pytest

def test_request_moves_through_prefill_and_decode():
    request = InferenceRequest(
        request_id="request-1",
        prompt_tokens=10,
        max_new_tokens=3,
        arrived_at=1.0,
    )

    state = RequestState(request=request)

    assert state.phase == RequestPhase.PREFILL
    assert state.generated_tokens == 0

    # Prefill 处理输入 Token，并得到第一个输出 Token。
    state.run_prefill()

    assert state.phase == RequestPhase.DECODE
    assert state.generated_tokens == 1

    # Decode 每一步为请求生成一个 Token
    state.run_decode_step()

    assert state.phase == RequestPhase.DECODE
    assert state.generated_tokens == 2

    state.run_decode_step()

    assert state.phase == RequestPhase.FINISHED
    assert state.generated_tokens == 3

def test_scheduler_refills_slots_between_steps():
    scheduler = BatchScheduler(
        max_active_requests=2,
        queue_capacity=3,
        max_batch_tokens=100,
        kv_cache_capacity_tokens=100,
    )

    for request_id, output_tokens in [
        ("A", 2),
        ("B", 3),
        ("C", 1),
    ]:
        scheduler.submit(
            InferenceRequest(
                request_id=request_id,
                prompt_tokens=10,
                max_new_tokens=output_tokens,
                arrived_at=0.0,
            )
        )

    assert scheduler.waiting_count == 3
    assert scheduler.active_count == 0

    #  AB 入场,分别完成 Prefill
    assert scheduler.step() == []
    assert scheduler.active_request_ids == ("A", "B")
    assert scheduler.waiting_count == 1

    # A 完成, B 继续, C 本轮在等待
    assert scheduler.step() == ["A"]
    assert scheduler.active_request_ids == ("B",)
    assert scheduler.waiting_count == 1

    # C 补入空槽, B 和 C 均完成
    assert scheduler.step() == ["B","C"]
    assert scheduler.active_count == 0
    assert scheduler.waiting_count == 0

    # 空闲调度器可以安全执行空轮次
    assert scheduler.step() == []

def test_scheduler_respects_per_step_token_budget():
    scheduler = BatchScheduler(
        max_active_requests=3,
        queue_capacity=3,
        max_batch_tokens=8,
        kv_cache_capacity_tokens=100,
    )

    for request_id, prompt_tokens in [
        ("A", 5),
        ("B", 3),
        ("C", 4),
    ]:
        scheduler.submit(
            InferenceRequest(
                request_id=request_id,
                prompt_tokens=prompt_tokens,
                max_new_tokens=1,
                arrived_at=0.0,

            )
        )

    # A 使用 5，B 使用 3，正好耗尽本轮预算。
    # C 留在活跃集合中，等待下一轮 Prefill。
    assert scheduler.step() == ["A", "B"]
    assert scheduler.last_step_token_count == 8
    assert scheduler.active_request_ids == ("C",)

    # C 完成 Prefill，并开始 Decode
    assert scheduler.step() == ["C"]
    assert scheduler.last_step_token_count == 4
    assert scheduler.active_count == 0

def test_scheduler_chunks_large_prefill_across_steps():
    scheduler = BatchScheduler(
        max_active_requests=1,
        queue_capacity=1,
        max_batch_tokens=4,
        kv_cache_capacity_tokens=100,
    )

    scheduler.submit(
        InferenceRequest(
            request_id="long-prompt",
            prompt_tokens=10,
            max_new_tokens=1,
            arrived_at=0.0,
        )
    )

    # 第一轮处理前 4 个输入 Token。
    assert scheduler.step() == []
    assert scheduler.last_step_token_count == 4

    # 第二轮再处理 4 个。
    assert scheduler.step() == []
    assert scheduler.last_step_token_count == 4

    # 第三轮处理剩余 2 个，Prefill 完成并产生首 Token。
    assert scheduler.step() == ["long-prompt"]
    assert scheduler.last_step_token_count == 2

def test_scheduler_tracks_and_releases_kv_cache():
    scheduler = BatchScheduler(
        max_active_requests=1,
        queue_capacity=1,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=10,
    )

    scheduler.submit(
        InferenceRequest(
            request_id="A",
            prompt_tokens=4,
            max_new_tokens=2,
            arrived_at=0.0,
        )
    )

    assert scheduler.kv_cache_used_tokens == 0

    # Prefill 占用 4 个 Token
    assert scheduler.step() == []
    assert scheduler.last_step_token_count == 4
    assert scheduler.kv_cache_used_tokens == 4

    # Decode 使用 1 个 Token, 随后请求结束释放全部 KV
    assert scheduler.step() == ["A"]
    assert scheduler.last_step_token_count == 1
    assert scheduler.kv_cache_used_tokens == 0

def test_finishing_request_only_releases_its_own_kv_cache():
    scheduler = BatchScheduler(
        max_active_requests=2,
        queue_capacity=2,
        max_batch_tokens=5,
        kv_cache_capacity_tokens=10,
    )

    # B 必须先加入，让 A 后完成本轮处理，
    # 从而暴露“完成一个请求就把总量清零”的问题。
    scheduler.submit(
        InferenceRequest(
            request_id="B",
            prompt_tokens=3,
            max_new_tokens=2,
            arrived_at=0.0,
        )
    )
    scheduler.submit(
        InferenceRequest(
            request_id="A",
            prompt_tokens=2,
            max_new_tokens=1,
            arrived_at=0.0,
        )
    )

    # B 完成 Prefill，占用 3；
    # A 完成整个请求，其 2 个 KV Token 随即释放。
    assert scheduler.step() == ["A"]

    assert scheduler.active_request_ids == ("B",)
    assert scheduler.kv_cache_used_tokens == 3

    # B Decode 一步后完成，释放自己的全部 KV。
    assert scheduler.step() == ["B"]
    assert scheduler.kv_cache_used_tokens == 0

def test_scheduler_rejects_request_that_can_never_fit_kv_cache():
    scheduler = BatchScheduler(
        max_active_requests=1,
        queue_capacity=1,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=4,
    )

    request = InferenceRequest(
        request_id="too-large",
        prompt_tokens=4,
        max_new_tokens=2,
        arrived_at=0.0,
    )

    # 峰值 KV 需求为 4 + 2 - 1 = 5，
    # 超过系统总容量 4，因此永远不可能完成。

    with pytest.raises(
        RequestKVCacheTooLargeError,
        match="KV Cache",
    ):
        scheduler.submit(request)

     # 被拒绝的请求不能污染任何内部状态。
    assert scheduler.waiting_count == 0
    assert scheduler.active_count == 0
    assert scheduler.kv_cache_used_tokens == 0

def test_scheduler_accepts_request_when_kv_shortage_is_temporary():
    scheduler = BatchScheduler(
        max_active_requests=2,
        queue_capacity=2,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=5,
    )

    scheduler.submit(
        InferenceRequest(
            request_id="A",
            prompt_tokens=4,
            max_new_tokens=2,
            arrived_at=0.0,
        )
    )

    # A 完成 Prefill，占用 4/5 个 KV Token。
    assert scheduler.step() == []
    assert scheduler.kv_cache_used_tokens == 4

    # B 峰值需求为 2，低于总容量 5，因此它是合法请求。
    # 尽管当前只剩 1 个 KV Token，也不能在 submit 时永久拒绝。
    scheduler.submit(
        InferenceRequest(
            request_id="B",
            prompt_tokens=2,
            max_new_tokens=1,
            arrived_at=1.0,
        )
    )

    assert scheduler.waiting_count == 1

    # 本轮开始时 A 仍预留全部容量，
    # 因此 B 保持等待；A 在本轮完成并释放资源。
    assert scheduler.step() == ["A"]

    assert scheduler.waiting_count == 1
    assert scheduler.active_count == 0
    assert scheduler.kv_cache_used_tokens == 0
    assert scheduler.kv_cache_reserved_tokens == 0

    # 下一轮 B 获得容量预留并执行。
    assert scheduler.step() == ["B"]

    assert scheduler.waiting_count == 0
    assert scheduler.active_count == 0
    assert scheduler.kv_cache_used_tokens == 0
    assert scheduler.kv_cache_reserved_tokens == 0

def test_scheduler_reserves_peak_kv_capacity_before_activation():
    scheduler = BatchScheduler(
        max_active_requests=2,
        queue_capacity=2,
        max_batch_tokens=4,
        kv_cache_capacity_tokens=4,
    )

    for request_id in ["A", "B"]:
        scheduler.submit(
            InferenceRequest(
                request_id=request_id,
                prompt_tokens=3,
                max_new_tokens=2,
                arrived_at=0.0,
            )
        )

     # 每个请求的峰值需求都是：
    # 3 + 2 - 1 = 4
    #
    # A 获得完整预留后，B 暂时不能进入活跃集合。
    assert scheduler.step() == []

    assert scheduler.active_request_ids == ("A",)
    assert scheduler.waiting_count == 1
    assert scheduler.kv_cache_used_tokens == 3
    assert scheduler.kv_cache_reserved_tokens == 4

    # A Decode 完成，释放实际占用和预留容量。
    assert scheduler.step() == ["A"]

    assert scheduler.active_count == 0
    assert scheduler.waiting_count == 1
    assert scheduler.kv_cache_used_tokens == 0
    assert scheduler.kv_cache_reserved_tokens == 0

    # 下一轮 B 获得预留并开始 Prefill。
    assert scheduler.step() == []

    assert scheduler.active_request_ids == ("B",)
    assert scheduler.waiting_count == 0
    assert scheduler.kv_cache_used_tokens == 3
    assert scheduler.kv_cache_reserved_tokens == 4

    # B 最终完成并释放资源。
    assert scheduler.step() == ["B"]

    assert scheduler.kv_cache_used_tokens == 0
    assert scheduler.kv_cache_reserved_tokens == 0

def test_scheduler_exposes_paged_kv_allocation():
    scheduler = BatchScheduler(
        max_active_requests=1,
        queue_capacity=1,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=16,
        kv_cache_block_size=4,
    )

    scheduler.submit(
        InferenceRequest(
            request_id="A",
            prompt_tokens=5,
            max_new_tokens=2,
            arrived_at=0.0,
        )
    )

    # 5 个逻辑 Token 需要两个 4-Token Block。
    assert scheduler.step() == []

    assert scheduler.kv_cache_used_tokens == 5
    assert scheduler.kv_cache_allocated_tokens == 8
    assert scheduler.kv_cache_reserved_tokens == 6

    # Decode 增加一个逻辑 Token，仍处于两个 Block 内；
    # 请求完成后释放全部资源。
    assert scheduler.step() == ["A"]

    assert scheduler.kv_cache_used_tokens == 0
    assert scheduler.kv_cache_allocated_tokens == 0
    assert scheduler.kv_cache_reserved_tokens == 0