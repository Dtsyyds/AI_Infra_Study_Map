from month02_rag_agent.retrieval_eval import (
    evaluate_rankings,
    recall_at_k,
    reciprocal_rank,
    load_eval_dataset,
    evaluate_retriever,
    run_retrieval_eval,
    save_eval_report,
    determine_retrieval_exit_code,
)

import json
from pathlib import Path
import pytest

import month02_rag_agent.retrieval_eval as retrieval_eval_module

def test_retrieval_metrics_measure_ranked_results():
    retrieved_ids = [
        "chunk-b",
        "chunk-a",
        "chunk-c",
    ]

    relevant_ids = {
        "chunk-a",
        "chunk-x",
    }

    assert recall_at_k(
        retrieved_ids,
        relevant_ids,
        k=1,
    ) == 0.0

    assert recall_at_k(
        retrieved_ids,
        relevant_ids,
        k=2,
    ) == 0.5

    assert reciprocal_rank(
        retrieved_ids,
        relevant_ids,
    ) == 0.5

def test_reciprocal_rank_returns_zero_when_no_relevant_result():
    assert reciprocal_rank(
        ["chunk-b", "chunk-c"],
        {"chunk-a"},
    ) == 0.0

def test_evaluate_rankings_reports_recall_and_mrr():
    cases = [
        (
            ["chunk-a", "chunk-b", "chunk-c"],
            {"chunk-a"},
        ),
        (
            ["chunk-x", "chunk-b", "chunk-c"],
            {"chunk-b"},
        ),
        (
            ["chunk-x", "chunk-y", "chunk-z"],
            {"chunk-missing"},
        ),
    ]

    summary = evaluate_rankings(
        cases,
        k=2,
    )

    assert summary["query_count"] == 3
    assert summary["k"] == 2
    assert summary["recall_at_k"] == pytest.approx(2 / 3)
    assert summary["mrr"] == pytest.approx(0.5)

def test_recall_at_k_does_not_double_count_duplicate_ids():
    result = recall_at_k(
        ["chunk-a", "chunk-a"],
        {"chunk-a"},
        k=2,
    )

    assert result == 1.0


class FakeEvalEmbedder:
    model_name = "fake-eval-model"

    query_vectors = {
        "沙盒负责什么？": [0.0, 1.0],
        "Runtime 负责什么？": [1.0, 0.0],
    }

    def embed_query(self, query):
        return self.query_vectors[query]

def test_evaluate_retriever_connects_search_and_metrics():
    records = [
        {
            "id": "runtime",
            "text": "Runtime 负责 Agent 循环调度",
            "embedding": [1.0, 0.0],
            "metadata": {},
        },
        {
            "id": "sandbox",
            "text": "沙盒负责限制文件访问范围",
            "embedding": [0.0, 1.0],
            "metadata": {},
        },
    ]

    cases = [
        {
            "id": "sandbox-question",
            "query": "沙盒负责什么？",
            "relevant_ids": ["sandbox"],
        },
        {
            "id": "runtime-question",
            "query": "Runtime 负责什么？",
            "relevant_ids": ["runtime"],
        },
    ]

    report = evaluate_retriever(
        cases=cases,
        records=records,
        embedder=FakeEvalEmbedder(),
        top_k=1,
    )

    assert report["summary"] == {
        "query_count": 2,
        "k": 1,
        "recall_at_k": 1.0,
        "mrr": 1.0,
    }

    assert report["case_results"][0] == {
        "id": "sandbox-question",
        "retrieved_ids": ["sandbox"],
        "relevant_ids": ["sandbox"],
        "recall_at_k": 1.0,
        "rr": 1.0,
    }

    assert report["case_results"][1]["retrieved_ids"] == [
        "runtime",
    ]

EVAL_DATASET_PATH= (
    Path(__file__).parents[1]
    / "eval_questions.json"
)

def test_load_eval_dataset_read_versioned_cases():
    dataset = load_eval_dataset(EVAL_DATASET_PATH,)

    assert dataset["schema_version"] == 1
    assert dataset["index_config"] == {
        "source": "month02_rag_agent/docs/agent_infra.md",
        "chunk_size": 200,
        "overlap": 40,
    }

    cases = dataset["cases"]

    assert len(cases) == 3
    assert {
        case["id"]
        for case in cases
    } == {
        "sandbox-purpose",
        "trace-records",
        "eval-purpose",
    }

    for case in cases:
        assert case["query"].strip()
        assert case["relevant_ids"]

def test_load_eval_dataset_rejects_missing_relevant_ids(
    tmp_path,
):
    path = tmp_path / "invalid_eval.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "invalid-case",
                        "query": "这是一个错误样本",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="relevant_ids",
    ):
        load_eval_dataset(path,)


@pytest.mark.parametrize(
    "dataset, expected_message",
    [
        (
            {
                "schema_version": True,
                "cases": [
                    {
                        "id": "case-1",
                        "query": "问题",
                        "relevant_ids": ["chunk-1"],
                    }
                ],
            },
            "schema_version",
        ),
        (
            {"schema_version": 1},
            "cases",
        ),
        (
            {
                "schema_version": 1,
                "cases": ["不是字典"],
            },
            r"cases\[0\]",
        ),
        (
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "case-1",
                        "query": "问题",
                        "relevant_ids": ["   "],
                    }
                ],
            },
            "relevant_ids",
        ),
    ],
)
def test_load_eval_dataset_rejects_invalid_schema(
    tmp_path,
    dataset,
    expected_message,
):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        load_eval_dataset(path)

def test_run_retrieval_eval_loads_files_and_returns_report(
    tmp_path,
):
    index_path = tmp_path / "index.json"
    eval_path = tmp_path / "eval.json"

    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "fake-eval-model",
                "dimension": 2,
                "normalized": False,
                "records": [
                    {
                        "id": "docs/runtime.md::chunk_0000",
                        "text": "Runtime 负责循环调度",
                        "embedding": [1.0, 0.0],
                        "metadata": {
                            "source": "docs/runtime.md",
                            "chunk_index": 0,
                        },
                    },
                    {
                        "id": "docs/sandbox.md::chunk_0000",
                        "text": "沙盒负责限制文件访问",
                        "embedding": [0.0, 1.0],
                        "metadata": {
                            "source": "docs/sandbox.md",
                            "chunk_index": 0,
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    eval_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "sandbox-question",
                        "query": "沙盒负责什么？",
                        "relevant_ids": [
                            "docs/sandbox.md::chunk_0000"
                        ],
                    },
                    {
                        "id": "runtime-question",
                        "query": "Runtime 负责什么？",
                        "relevant_ids": [
                            "docs/runtime.md::chunk_0000"
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_retrieval_eval(
        eval_path=eval_path,
        index_path=index_path,
        embedder=FakeEvalEmbedder(),
        top_k=1,
    )

    assert report["schema_version"] == 1

    assert report["index"] == {
        "model": "fake-eval-model",
        "dimension": 2,
        "record_count": 2,
    }

    assert report["summary"] == {
        "query_count": 2,
        "k": 1,
        "recall_at_k": 1.0,
        "mrr": 1.0,
    }

    assert len(report["case_results"]) == 2

def test_save_eval_report_atomically_replaces_old_file(
        tmp_path,
):
    output_path = tmp_path / "retrieval_report.json"

    output_path.write_text(
        '{"old": true}',
        encoding="utf-8",
    )

    report = {
        "schema_version": 1,
        "summary": {
            "query_count": 2,
            "k": 1,
            "recall_at_k": 1.0,
            "mrr": 1.0,
        },
        "case_results": [],
    }

    save_eval_report(report, output_path,)

    saved_report = json.loads(
        output_path.read_text(encoding="utf-8")
    )

    assert saved_report == report
    assert list(tmp_path.glob("*.tmp")) == []

@pytest.mark.parametrize(
    "recall_score, mrr_score, expected_exit_code",
    [
        (0.80, 0.70, 0),  # 等于阈值
        (0.90, 0.80, 0),  # 全部高于阈值
        (0.79, 0.90, 1),  # Recall 不达标
        (0.90, 0.69, 1),  # MRR 不达标
    ],
)
def test_retrieval_gate_enforces_metric_thresholds(
    recall_score,
    mrr_score,
    expected_exit_code,
):
    report = {
        "summary": {
            "recall_at_k": recall_score,
            "mrr": mrr_score,
        }
    }

    exit_code = determine_retrieval_exit_code(
        report,
        min_recall_at_k=0.80,
        min_mrr=0.70,
    )

    assert exit_code == expected_exit_code

def test_retrieval_gate_rejects_nan_metric():
    report = {
        "summary": {
            "recall_at_k": float("nan"),
            "mrr": 1.0,
        }
    }

    with pytest.raises(
        ValueError,
        match="recall_at_k",
    ):
        determine_retrieval_exit_code(
            report,
            min_recall_at_k=0.8,
            min_mrr=0.7,
        )

@pytest.mark.parametrize(
    "recall_score, expected_exit_code",
    [
        (0.90, 0),
        (0.70, 1),
    ],
)
def test_retrieval_eval_main_saves_report_and_returns_gate_code(
    tmp_path,
    monkeypatch,
    recall_score,
    expected_exit_code,
):
    report = {
        "schema_version": 1,
        "index": {
            "model": "fake-eval-model",
            "dimension": 2,
            "record_count": 2,
        },
        "summary": {
            "query_count": 2,
            "k": 2,
            "recall_at_k": recall_score,
            "mrr": 0.90,
        },
        "case_results": [],
    }

    def fake_run_retrieval_eval(
        eval_path,
        index_path,
        embedder,
        *,
        top_k,
    ):
        assert Path(eval_path).name == "eval.json"
        assert Path(index_path).name == "index.json"
        assert isinstance(embedder, FakeEvalEmbedder)
        assert top_k == 2
        return report

    monkeypatch.setattr(
        retrieval_eval_module,
        "run_retrieval_eval",
        fake_run_retrieval_eval,
    )

    output_path = tmp_path / "report.json"

    exit_code = retrieval_eval_module.main(
        [
            str(tmp_path / "eval.json"),
            str(tmp_path / "index.json"),
            str(output_path),
            "--top-k",
            "2",
            "--min-recall-at-k",
            "0.8",
            "--min-mrr",
            "0.7",
        ],
        embedder_factory=FakeEvalEmbedder,
    )

    assert exit_code == expected_exit_code
    assert json.loads(
        output_path.read_text(encoding="utf-8")
    ) == report