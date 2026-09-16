from month03_llm_serving.serving_metrics import (
    RequestTiming,
    summarize_request_timing,
)

import pytest

def test_request_timing_separates_serving_phases():
    timing = RequestTiming(
        arrived_at=10.0,
        prefill_started_at=11.0,
        first_token_at=13.0,
        completed_at=19.0,
        output_tokens=4,
    )

    assert summarize_request_timing(timing) == {
        "queue_time": 1.0,
        "prefill_time": 2.0,
        "ttft": 3.0,
        "decode_time": 6.0,
        "tpot": 2.0,
        "e2e_latency": 9.0,
    }

def test_single_output_token_has_no_tpot():
    timing = RequestTiming(
        arrived_at=10.0,
        prefill_started_at=11.0,
        first_token_at=13.0,
        completed_at=13.0,
        output_tokens=1,
    )

    metrics = summarize_request_timing(timing)

    assert metrics["tpot"] is None
    assert metrics["decode_time"] == 0.0


@pytest.mark.parametrize(
    "timing",
    [
        RequestTiming(2.0, 1.0, 3.0, 4.0, 2),
        RequestTiming(1.0, 3.0, 2.0, 4.0, 2),
        RequestTiming(1.0, 2.0, 4.0, 3.0, 2),
    ],
)
def test_request_timing_rejects_invalid_time_order(timing):
    with pytest.raises(ValueError, match="时间顺序"):
        summarize_request_timing(timing)


def test_request_timing_rejects_zero_output_tokens():
    timing = RequestTiming(
        arrived_at=1.0,
        prefill_started_at=2.0,
        first_token_at=3.0,
        completed_at=4.0,
        output_tokens=0,
    )

    with pytest.raises(ValueError, match="output_tokens"):
        summarize_request_timing(timing)