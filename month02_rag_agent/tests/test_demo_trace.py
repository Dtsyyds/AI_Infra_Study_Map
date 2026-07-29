import json
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_status",
        "expected_answer",
        "expected_citations",
        "expected_retrieved_count",
        "expected_llm_calls",
        "expected_retries",
    ),
    [
        (
            "success",
            "success",
            "沙盒负责限制文件访问范围，防止越界读写 [S1]",
            ["S1"],
            1,
            1,
            0,
        ),
        (
            "no-context",
            "no_context",
            "资料不足，无法回答",
            [],
            0,
            0,
            0,
        ),
        (
            "citation-refused",
            "citation_refused",
            "资料不足，无法回答",
            [],
            1,
            2,
            1,
        ),
    ],
)
def test_demo_trace_cli_outputs_expected_contract(
    scenario,
    expected_status,
    expected_answer,
    expected_citations,
    expected_retrieved_count,
    expected_llm_calls,
    expected_retries,
):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "month02_rag_agent.demo_trace",
            "--scenario",
            scenario,
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

    payload = json.loads(completed.stdout)
    trace = payload["trace"]

    assert payload["scenario"] == scenario
    assert payload["answer"] == expected_answer

    assert trace["status"] == expected_status
    assert trace["citations"] == expected_citations
    assert trace["retrieved_count"] == expected_retrieved_count
    assert trace["llm_calls"] == expected_llm_calls
    assert trace["citation_retries"] == expected_retries
    assert trace["duration_seconds"] >= 0.0

    if expected_retrieved_count == 0:
        assert trace["top_score"] is None
    else:
        assert trace["top_score"] == pytest.approx(1.0)