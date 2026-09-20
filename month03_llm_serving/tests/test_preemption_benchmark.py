from month03_llm_serving.benchmark_preemption import (
    build_preemption_report,
)


def test_preemption_report_exposes_latency_tradeoff():
    report = build_preemption_report()

    assert report["unit"] == "scheduler_step"

    without_preemption = report["policies"]["none"]
    with_preemption = report["policies"]["recompute_last"]

    assert without_preemption["completion_steps"] == {
        "A": 2,
        "C": 2,
        "B": 3,
    }
    assert with_preemption["completion_steps"] == {
        "B": 1,
        "A": 2,
        "C": 4,
    }

    assert without_preemption["latency_steps"]["B"] == 3
    assert with_preemption["latency_steps"]["B"] == 1

    assert without_preemption["total_preemptions"] == 0
    assert with_preemption["total_preemptions"] == 1

    assert without_preemption["total_recomputed_tokens"] == 0
    assert with_preemption["total_recomputed_tokens"] == 3

    assert report["comparison"] == {
        "short_request_latency_delta_steps": -2,
        "makespan_delta_steps": 1,
        "extra_recomputed_tokens": 3,
    }