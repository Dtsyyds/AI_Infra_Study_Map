from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class RequestTiming:
    arrived_at: float
    prefill_started_at: float
    first_token_at: float
    completed_at: float
    output_tokens: int

"""
    指标实现
    queue_time: 请求到达队列的时间      prefill_started_at - arrived_at
    prefill_time: 填充时间            first_token_at - prefill_started_at
    ttft: 从队列到第一个token的时间     first_token_at - arrived_at
    decode_time: 解码时间              completed_at - first_token_at
    tpot: Time Per Output Token      decode_time / (output_tokens - 1)
    e2e_latency: 端到端延迟             completed_at - arrived_at
"""

def summarize_request_timing(timing: RequestTiming) -> dict[str, float | None]:
    if timing.output_tokens < 1:
        raise ValueError("output_tokens 必须大于等于 1")

    if not (
        timing.arrived_at
        <= timing.prefill_started_at
        <= timing.first_token_at
        <= timing.completed_at
    ):
        raise ValueError("时间顺序不合法")
    
    queue_time = timing.prefill_started_at - timing.arrived_at
    prefill_time = timing.first_token_at - timing.prefill_started_at
    ttft = timing.first_token_at - timing.arrived_at
    decode_time = timing.completed_at - timing.first_token_at
    tpot = decode_time / (timing.output_tokens - 1) if timing.output_tokens > 1 else None
    e2e_latency = timing.completed_at - timing.arrived_at

    return {
        "queue_time": queue_time,
        "prefill_time": prefill_time,
        "ttft": ttft,
        "decode_time": decode_time,
        "tpot": tpot,
        "e2e_latency": e2e_latency,
    }