import pytest

from month02_rag_agent.retriever import cosine_similarity, search


RECORDS = [
    {
        "id": "runtime",
        "text": "Agent Runtime 负责循环调度",
        "embedding": [0.9, 0.1, 0.0, 0.0],
        "metadata": {},
    },
    {
        "id": "sandbox",
        "text": "沙盒负责限制文件访问范围",
        "embedding": [0.1, 0.9, 0.0, 0.0],
        "metadata": {},
    },
    {
        "id": "trace",
        "text": "Trace 记录 Agent 执行轨迹",
        "embedding": [0.0, 0.1, 0.9, 0.0],
        "metadata": {},
    },
    {
        "id": "memory",
        "text": "Memory 保存任务上下文",
        "embedding": [0.0, 0.0, 0.1, 0.9],
        "metadata": {},
    },
]


@pytest.mark.parametrize(
    "vector_a, vector_b, expected",
    [
        ([1, 0], [1, 0], 1.0),
        ([1, 0], [0, 1], 0.0),
        ([1, 0], [-1, 0], -1.0),
        ([1, 1], [2, 2], 1.0),
    ],
)
def test_cosine_similarity(vector_a, vector_b, expected):
    assert cosine_similarity(vector_a, vector_b) == pytest.approx(expected)


def test_empty_vector():
    with pytest.raises(ValueError):
        cosine_similarity([], [])


def test_dimension_mismatch():
    with pytest.raises(ValueError):
        cosine_similarity([1, 2], [1])


def test_zero_vector():
    with pytest.raises(ValueError):
        cosine_similarity([0, 0], [1, 1])


def test_search_top1():
    result = search([0.2, 0.95, 0.0, 0.0], RECORDS, top_k=1)

    assert len(result) == 1
    assert result[0]["id"] == "sandbox"
    assert set(result[0]) == {"id", "text", "metadata", "score"}
    assert isinstance(result[0]["score"], float)


def test_top_k_larger_than_records():
    result = search([0.2, 0.95, 0.0, 0.0], RECORDS, top_k=10)

    assert len(result) == len(RECORDS)


def test_empty_records():
    assert search([1, 0], [], top_k=3) == []


@pytest.mark.parametrize("top_k", [0, -1])
def test_invalid_top_k(top_k):
    with pytest.raises(ValueError):
        search([1, 0], RECORDS, top_k=top_k)


def test_invalid_top_k_with_empty_records():
    with pytest.raises(ValueError):
        search([1, 0], [], top_k=0)


def test_search_dimension_mismatch():
    records = [
        {
            "id": "bad",
            "text": "维度错误",
            "embedding": [1, 0, 0],
            "metadata": {},
        }
    ]

    with pytest.raises(ValueError):
        search([1, 0], records, top_k=1)

def test_search_filters_by_min_score():
    result = search([0.2, 0.95, 0.0, 0.0], RECORDS, top_k=3, min_score=0.5)

    assert len(result) == 1
    assert result[0]["id"] == "sandbox"

def test_search_returns_empty_when_all_scores_below_threshold():
    result = search([0.2, 0.95, 0.0, 0.0], RECORDS, top_k=3, min_score=1.0)

    assert len(result) == 0

@pytest.mark.parametrize("min_score", [-1.1, 1.1, "0.5"])
def test_search_rejects_invalid_min_score(min_score):
    with pytest.raises(ValueError):
        search([0.2, 0.95, 0.0, 0.0], RECORDS, top_k=3, min_score=min_score)
