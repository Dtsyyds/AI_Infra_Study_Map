from month03_llm_serving.request_queue import InferenceRequest
from month03_llm_serving.scheduler import (
    BatchScheduler,
    PreemptionPolicy,
)
from month03_llm_serving.simulation import (
    ScheduledRequest,
    run_workload,
)


def run_test_workload(
        policy: PreemptionPolicy,
):
    scheduler = BatchScheduler(
        max_active_requests=3,
        queue_capacity=3,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=10,
        preemption_policy=policy,
        max_preemptions_per_request=1,
    )

    workload = [
        ScheduledRequest(
            arrival_step=0,
            request=InferenceRequest(
                request_id="A",
                prompt_tokens=3,
                max_new_tokens=3,
                arrived_at=0.0,
            ),
        ),
        ScheduledRequest(
            arrival_step=0,
            request=InferenceRequest(
                request_id="C",
                prompt_tokens=3,
                max_new_tokens=3,
                arrived_at=0.0,
            ),
        ),
        ScheduledRequest(
            arrival_step=1,
            request=InferenceRequest(
                request_id="B",
                prompt_tokens=2,
                max_new_tokens=1,
                arrived_at=1.0,
            ),
        ),
    ]

    return run_workload(
        scheduler=scheduler,
        workload=workload,
        max_steps=20,
    )

def test_recompute_preemption_trades_extra_work_for_shorter_wait():
    without_preemption = run_test_workload(
        policy=PreemptionPolicy.NONE,
    )
    with_preemption = run_test_workload(
        policy=PreemptionPolicy.RECOMPUTE_LAST,
    )

    assert without_preemption.completion_steps == {
        "A": 2,
        "C": 2,
        "B": 3,
    }

    assert with_preemption.completion_steps == {
        "B": 1,
        "A": 2,
        "C": 4,
    }

    # 抢占让后到达的短请求 B 更早完成。
    assert (
        with_preemption.completion_steps["B"]
        < without_preemption.completion_steps["B"]
    )

    # 代价是受害者 C 更晚完成，并产生重计算。
    assert (
        with_preemption.completion_steps["C"]
        > without_preemption.completion_steps["C"]
    )

    assert without_preemption.total_preemptions == 0
    assert without_preemption.total_recomputed_tokens == 0

    assert with_preemption.total_preemptions == 1
    assert with_preemption.total_recomputed_tokens == 3

    assert without_preemption.steps_executed == 4
    assert with_preemption.steps_executed == 5