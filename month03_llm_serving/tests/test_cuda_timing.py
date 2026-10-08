import pytest

torch = pytest.importorskip("torch")

from month03_llm_serving.profiling.cuda_timing import (
    measure_cuda_event_ms,
    measure_submission_ms,
    measure_synchronized_ms,
    warm_up,
)
from month03_llm_serving.profiling import cuda_timing

def test_warm_up_rejects_negative_iterations():
    with pytest.raises(
        ValueError,
        match="iterations must be non-negative",
    ):
        warm_up(
            operation=lambda: torch.tensor(0),
            iterations=-1,
        )


@pytest.mark.skipif(
    not torch.cuda.is_available(),
    reason="CUDA device is not available",
)
def test_cuda_timing_functions_return_positive_values():
    device = torch.device("cuda")

    left = torch.randn(
        (512, 512),
        device=device,
        dtype=torch.float32,
    )
    right = torch.randn(
        (512, 512),
        device=device,
        dtype=torch.float32,
    )

    def operation() -> torch.Tensor:
        return left @ right

    warm_up(operation, iterations=3)

    submission_ms = measure_submission_ms(operation)
    synchronized_ms = measure_synchronized_ms(operation)
    cuda_event_ms = measure_cuda_event_ms(operation)

    assert submission_ms > 0
    assert synchronized_ms > 0
    assert cuda_event_ms > 0

def test_submission_timing_converts_seconds_to_ms(
    monkeypatch,
):
    cuda_checks = []
    synchronize_calls = []
    operation_calls = []

    timestamps = iter([
        10.0,
        10.002,
    ])

    monkeypatch.setattr(
        cuda_timing,
        "require_cuda",
        lambda: cuda_checks.append("checked"),
    )
    monkeypatch.setattr(
        cuda_timing.torch.cuda,
        "synchronize",
        lambda: synchronize_calls.append("sync"),
    )
    monkeypatch.setattr(
        cuda_timing.time,
        "perf_counter",
        lambda: next(timestamps),
    )

    duration_ms = cuda_timing.measure_submission_ms(
        lambda: operation_calls.append("operation")
    )

    assert duration_ms == pytest.approx(2.0)
    assert cuda_checks == ["checked"]
    assert operation_calls == ["operation"]
    assert synchronize_calls == ["sync", "sync"]


def test_synchronized_timing_converts_seconds_to_ms(
    monkeypatch,
):
    synchronize_calls = []
    operation_calls = []

    timestamps = iter([
        20.0,
        20.003,
    ])

    monkeypatch.setattr(
        cuda_timing,
        "require_cuda",
        lambda: None,
    )
    monkeypatch.setattr(
        cuda_timing.torch.cuda,
        "synchronize",
        lambda: synchronize_calls.append("sync"),
    )
    monkeypatch.setattr(
        cuda_timing.time,
        "perf_counter",
        lambda: next(timestamps),
    )

    duration_ms = (
        cuda_timing.measure_synchronized_ms(
            lambda: operation_calls.append(
                "operation"
            )
        )
    )

    assert duration_ms == pytest.approx(3.0)
    assert operation_calls == ["operation"]
    assert synchronize_calls == ["sync", "sync"]