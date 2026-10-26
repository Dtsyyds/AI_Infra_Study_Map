import pytest

from month04_deploy.app.durable.backoff import (
    compute_retry_delay,
)


@pytest.mark.parametrize(
    ("attempts_used", "fraction", "expected"),
    [
        (1, 1.0, 0.5),
        (2, 1.0, 1.0),
        (3, 1.0, 2.0),
        (4, 1.0, 4.0),
        (5, 1.0, 4.0),
        (100_000, 1.0, 4.0),
        (3, 0.0, 0.0),
        (3, 0.25, 0.5),
        (3, 0.5, 1.0),
    ],
)
def test_retry_delay(attempts_used, fraction, expected):
    actual = compute_retry_delay(
        attempts_used=attempts_used,
        base_seconds=0.5,
        max_seconds=4.0,
        random_fraction=fraction,
    )

    assert actual == pytest.approx(expected)


def test_base_larger_than_cap():
    actual = compute_retry_delay(
        attempts_used=1,
        base_seconds=10.0,
        max_seconds=4.0,
        random_fraction=0.5,
    )

    assert actual == pytest.approx(2.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempts_used": 0},
        {"base_seconds": 0.0},
        {"max_seconds": -1.0},
        {"base_seconds": float("nan")},
        {"max_seconds": float("inf")},
        {"random_fraction": -0.1},
        {"random_fraction": 1.1},
    ],
)
def test_invalid_parameters(overrides):
    arguments = {
        "attempts_used": 1,
        "base_seconds": 0.5,
        "max_seconds": 4.0,
        "random_fraction": 0.5,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError):
        compute_retry_delay(**arguments)