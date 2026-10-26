import pytest

from month04_deploy.app.durable.retry_policy import (
    RetryDecision,
    decide_retry,
)


@pytest.mark.parametrize(
    (
        "retryable_error",
        "outcome_unknown",
        "replay_safe",
        "attempts_used",
        "max_attempts",
        "time_budget_remaining",
        "expected",
    ),
    [
        pytest.param(
            False, False, False, 1, 3, True,
            RetryDecision.STOP,
            id="permanent-error",
        ),
        pytest.param(
            True, False, True, 1, 3, True,
            RetryDecision.RETRY,
            id="transient-read",
        ),
        pytest.param(
            True, False, False, 1, 3, True,
            RetryDecision.RETRY,
            id="confirmed-no-side-effect",
        ),
        pytest.param(
            True, True, False, 1, 3, True,
            RetryDecision.RECONCILE,
            id="unknown-unsafe-operation",
        ),
        pytest.param(
            True, True, True, 1, 3, True,
            RetryDecision.RETRY,
            id="unknown-but-safe-to-replay",
        ),
        pytest.param(
            True, False, True, 2, 3, True,
            RetryDecision.RETRY,
            id="last-attempt-still-available",
        ),
        pytest.param(
            True, False, True, 3, 3, True,
            RetryDecision.STOP,
            id="attempt-limit-reached",
        ),
        pytest.param(
            True, False, True, 4, 3, True,
            RetryDecision.STOP,
            id="attempt-limit-exceeded",
        ),
        pytest.param(
            True, False, True, 1, 3, False,
            RetryDecision.STOP,
            id="time-budget-exhausted",
        ),
        pytest.param(
            True, True, False, 3, 3, False,
            RetryDecision.RECONCILE,
            id="exhausted-budget-does-not-resolve-unknown",
        ),
        pytest.param(
            False, True, False, 1, 3, True,
            RetryDecision.RECONCILE,
            id="nonretryable-error-with-unknown-outcome",
        ),
    ],
)
def test_retry_decision(
    retryable_error,
    outcome_unknown,
    replay_safe,
    attempts_used,
    max_attempts,
    time_budget_remaining,
    expected,
):
    actual = decide_retry(
        retryable_error=retryable_error,
        outcome_unknown=outcome_unknown,
        replay_safe=replay_safe,
        attempts_used=attempts_used,
        max_attempts=max_attempts,
        time_budget_remaining=time_budget_remaining,
    )

    assert actual == expected


@pytest.mark.parametrize(
    ("attempts_used", "max_attempts"),
    [
        (0, 3),
        (1, 0),
    ],
)
def test_invalid_attempt_counts(attempts_used, max_attempts):
    with pytest.raises(ValueError):
        decide_retry(
            retryable_error=True,
            outcome_unknown=False,
            replay_safe=True,
            attempts_used=attempts_used,
            max_attempts=max_attempts,
            time_budget_remaining=True,
        )