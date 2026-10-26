import math


def compute_retry_delay(
    *,
    attempts_used: int,
    base_seconds: float,
    max_seconds: float,
    random_fraction: float,
) -> float:
    """计算 Full Jitter 等待秒数；不执行等待。"""

    if attempts_used < 1:
        raise ValueError("attempts_used 必须至少为 1")

    if not math.isfinite(base_seconds) or base_seconds <= 0:
        raise ValueError("base_seconds 必须为有限正数")

    if not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds 必须为有限正数")

    if not 0 <= random_fraction <= 1:
        raise ValueError("random_fraction 必须在 0～1 之间")

    # 通过逐次翻倍并封顶，避免对极大的次数直接计算巨大指数。
    window = min(base_seconds, max_seconds)
    remaining_doublings = attempts_used - 1

    while remaining_doublings > 0 and window < max_seconds:
        # TODO 1：
        # 将 window 翻倍，但不得超过 max_seconds。
        # 将结果重新赋给 window。
        window *= 2
        window = min(window, max_seconds)

        remaining_doublings -= 1

    # TODO 2：
    # 根据 random_fraction 和 window 返回实际等待秒数。
    return random_fraction * window