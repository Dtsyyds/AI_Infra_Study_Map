import json

from month03_llm_serving.request_queue import (
    InferenceRequest,
)
from month03_llm_serving.scheduler import (
    BatchScheduler,
    PreemptionPolicy,
)
from month03_llm_serving.simulation import (
    ScheduledRequest,
    SimulationResult,
    run_workload,
)

def build_scheduler(
    policy: PreemptionPolicy,
) -> BatchScheduler:
    return BatchScheduler(
        max_active_requests=3,
        queue_capacity=3,
        max_batch_tokens=10,
        kv_cache_capacity_tokens=10,
        preemption_policy=policy,
        max_preemptions_per_request=1,
    )

def build_workload() -> list[ScheduledRequest]:
    return [
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

def result_to_report(
    result: SimulationResult,
    arrival_steps: dict[str, int],
) -> dict[str, object]:
    return {
        "steps_executed": result.steps_executed,
        "completion_steps": result.completion_steps,
        "latency_steps": calculate_latency_steps(
            result,
            arrival_steps,
        ),
        "total_preemptions": result.total_preemptions,
        "total_recomputed_tokens": (
            result.total_recomputed_tokens
        ),
    }

def calculate_latency_steps(
    result: SimulationResult,
    arrival_steps: dict[str, int],
) -> dict[str, int]:
    return {
        request_id: (
            completion_step
            - arrival_steps[request_id]
            + 1
        )
        for request_id, completion_step
        in result.completion_steps.items()
    }

def build_preemption_report() -> dict[str, object]:
    workload = build_workload()

    arrival_steps = {
        item.request.request_id: item.arrival_step
        for item in workload
    }
    """
    等价于:
    arrival_steps = {}

    for item in workload:
        request_id = item.request.request_id
        arrival_step = item.arrival_step
        arrival_steps[request_id] = arrival_step
    """
    result_without = run_workload(
        scheduler=build_scheduler(
            PreemptionPolicy.NONE,
        ),
        workload=workload,
        max_steps=20,
    )

    result_with = run_workload(
        scheduler=build_scheduler(
            PreemptionPolicy.RECOMPUTE_LAST,
        ),
        workload=workload,
        max_steps=20,
    )

    without_report = result_to_report(
        result_without,
        arrival_steps,
    )
    with_report = result_to_report(
        result_with,
        arrival_steps,
    )

    without_latency = without_report["latency_steps"]
    with_latency = with_report["latency_steps"]

    # 这里两个值的实际类型都是 dict[str, int]。
    assert isinstance(without_latency, dict)
    assert isinstance(with_latency, dict)
    return {
        "unit": "scheduler_step",
        "policies": {
            "none": without_report,
            "recompute_last": with_report,
        },
        "comparison": {
            "short_request_latency_delta_steps": (
                with_latency["B"]
                - without_latency["B"]
            ),
            "makespan_delta_steps": (
                result_with.steps_executed
                - result_without.steps_executed
            ),
            "extra_recomputed_tokens": (
                result_with.total_recomputed_tokens
                - result_without.total_recomputed_tokens
            ),
        },
    }

if __name__ == "__main__":
    print(
        json.dumps(
            build_preemption_report(),
            indent=2,
            sort_keys=True,
        )
    )
