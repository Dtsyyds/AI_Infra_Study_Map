from collections import deque
from dataclasses import dataclass

from month03_llm_serving.request_queue import InferenceRequest
from month03_llm_serving.scheduler import BatchScheduler

@dataclass(frozen=True, slots=True)
class ScheduledRequest:
    arrival_step: int
    request: InferenceRequest

@dataclass(frozen=True, slots=True)
class SimulationResult:
    steps_executed: int
    completion_steps: dict[str, int]
    total_preemptions: int
    total_recomputed_tokens: int

def run_workload(
        *,
        scheduler: BatchScheduler,
        workload: list[ScheduledRequest],
        max_steps: int,
) -> SimulationResult:
    """
    每个模拟 Step 按这个顺序执行：

        找出 arrival_step <= current_step 的请求；
        按原始顺序调用 scheduler.submit()；
        调用一次 scheduler.step()；
        记录本轮返回的完成请求；
        判断系统是否已经完全空闲；
        如果空闲，返回 SimulationResult；
        超过 max_steps 仍未完成，则抛出 RuntimeError。

    """
    if (
        isinstance(max_steps, bool)
        or not isinstance(max_steps, int)
        or max_steps < 1
    ):
        raise ValueError("max_steps 必须是正整数")

    # sorted 是稳定排序：
    # arrival_step 相同时，保持 workload 中的原始顺序。
    pending = deque(
        sorted(
            workload,
            key=lambda item: item.arrival_step,
        )
    )

    completion_steps : dict[str, int] = {}

    # 整个 scheduler 共享一条时间线
    for step in range(max_steps):
        # 提交本轮及此时已经到达的全部请求
        while(
            pending
            and pending[0].arrival_step <= step
        ):
            scheduled_request = pending.popleft()
            scheduler.submit(
                scheduled_request.request
            )

        # 即使返回 []，也必须进入下一轮。
        # [] 可能表示请求刚完成 Prefill 或仍在 Decode。
        completed_request_ids = scheduler.step()

        for request_id in completed_request_ids:
            completion_steps[request_id] = step

        simulation_finished = (
            not pending
            and scheduler.waiting_count == 0
            and scheduler.active_count == 0
            and scheduler.preempted_count == 0
        )

        if simulation_finished:
            return SimulationResult(
                steps_executed=step + 1,
                completion_steps=completion_steps,
                total_preemptions=scheduler.total_preemptions,
                total_recomputed_tokens=scheduler.total_recomputed_tokens,
            )

    raise RuntimeError(f"运行 {max_steps} 个 Step 后仍有请求未完成")
