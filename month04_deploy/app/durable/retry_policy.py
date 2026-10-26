from enum import Enum


class RetryDecision(str, Enum):
    RETRY = "retry"
    STOP = "stop"
    RECONCILE = "reconcile"


def decide_retry(
    *,
    retryable_error: bool,
    outcome_unknown: bool,
    replay_safe: bool,
    attempts_used: int,
    max_attempts: int,
    time_budget_remaining: bool,
) -> RetryDecision:
    """在一次未确认成功的尝试结束后，决定下一步动作。"""

    if max_attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")

    if attempts_used < 1:
        raise ValueError("attempts_used 必须至少为 1")

    # TODO 1：
    # 结果未知，并且不能安全重放：
    # 返回 RetryDecision.RECONCILE。
    if outcome_unknown and not replay_safe:
        return RetryDecision.RECONCILE

    # TODO 2：
    # 错误不值得重试：
    # 返回 RetryDecision.STOP。
    if not retryable_error:
        return RetryDecision.STOP

    # TODO 3：
    # 已使用次数达到或超过上限，或者没有剩余时间预算：
    # 返回 RetryDecision.STOP。
    if attempts_used >= max_attempts or not time_budget_remaining:
        return RetryDecision.STOP

    # TODO 4：
    # 其余情况返回 RetryDecision.RETRY。
    return RetryDecision.RETRY