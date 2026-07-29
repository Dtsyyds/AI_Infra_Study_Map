import pytest
from types import SimpleNamespace

from month02_rag_agent.indexer import build_index, save_index
from month02_rag_agent.rag_chain import retrieve_context, format_context, build_rag_prompt, answer_query, validate_answer_citations, run_rag_query
from month02_rag_agent.observability import RAGStatus
import month02_rag_agent.rag_chain as rag_chain_module


class FakeQueryEmbedder:
    model_name = "fake-embedding-v1"

    def embed_query(self, query):
        return [0.2, 0.95, 0.0, 0.0]

def test_retrieve_context_end_to_end(tmp_path):
    index = build_index(
        chunks = [
            "Agent Runtime 负责循环调度",
            "沙盒负责限制文件访问范围",
        ],
        embeddings=[
            [0.9, 0.1, 0.0, 0.0],
            [0.1, 0.9, 0.0, 0.0],
        ],
        source="docs/agent_infra.md",
        model_name="fake-embedding-v1",
    )

    index_path = tmp_path / "index.json"
    save_index(index, index_path)

    results = retrieve_context(
        query="如何限制文件访问?",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        top_k=1,
        min_score=0.5,
    )

    assert len(results) == 1
    assert results[0]["id"] == "docs/agent_infra.md::chunk_0001"

def test_format_context_preserves_rank_and_ids():
    results = [
        {
            "id": "docs/agent_infra.md::chunk_0001",
            "text": "沙盒负责限制文件访问范围",
            "metadata": {},
            "score": 0.98,
        },
        {
            "id": "docs/agent_infra.md::chunk_0000",
            "text": "Agent Runtime 负责循环调度",
            "metadata": {},
            "score": 0.76
        }
    ]

    content = format_context(results)

    assert content == (
        "[S1]\n"
        "id: docs/agent_infra.md::chunk_0001\n"
        "content: 沙盒负责限制文件访问范围\n\n"
        "[S2]\n"
        "id: docs/agent_infra.md::chunk_0000\n"
        "content: Agent Runtime 负责循环调度"
    )

def test_format_context_returns_empty_string_for_no_results():
    assert format_context([]) == ""

def test_build_rag_prompt_includes_question_context_and_rules():
    results = [
        {
            "id": "docs/agent_infra.md::chunk_0001",
            "text": "沙盒负责限制文件访问范围",
            "metadata": {},
            "score": 0.98,
        }
    ]

    prompt = build_rag_prompt(
        query="什么组件负责限制文件访问？",
        results=results,
    )

    assert "问题：\n什么组件负责限制文件访问？" in prompt
    assert "[S1]" in prompt
    assert "id: docs/agent_infra.md::chunk_0001" in prompt
    assert "content: 沙盒负责限制文件访问范围" in prompt
    assert "只能依据检索资料回答" in prompt
    assert "必须使用 [S1]、[S2] 形式标注引用" in prompt

def test_build_rag_prompt_rejects_empty_results():
    with pytest.raises(ValueError, match="检索结果"):
        build_rag_prompt(
            query="什么是 Agent Runtime？",
            results=[],
        )

class NoMatchQueryEmbedder:
    model_name = "fake-embedding-v1"

    def embed_query(self, query):
        # 与测试索引中的两个向量都正交
        return [0.0, 0.0, 1.0, 0.0]

class RecordingLLM:
    def __init__(
        self,
        response="沙盒负责限制文件访问范围 [S1]"
    ):
        self.response = response
        self.prompts= []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response

def create_test_index(tmp_path):
    index = build_index(
        chunks=[
            "Agent Runtime 负责循环调度",
            "沙盒负责限制文件访问范围",
        ],
        embeddings=[
            [0.9, 0.1, 0.0, 0.0],
            [0.1, 0.9, 0.0, 0.0],
        ],
        source="docs/agent_infra.md",
        model_name="fake-embedding-v1",
    )

    index_path = tmp_path / "index.json"
    save_index(index, index_path)
    return index_path

def test_answer_query_calls_llm_when_context_exists(tmp_path):
    index_path = create_test_index(tmp_path)

    llm = RecordingLLM()

    answer = answer_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
    )

    assert answer == "沙盒负责限制文件访问范围 [S1]"
    assert len(llm.prompts) == 1
    assert "问题：\n什么组件负责限制文件访问？" in llm.prompts[0]
    assert "沙盒负责限制文件访问范围" in llm.prompts[0]
    assert "[S1]" in llm.prompts[0]

def test_answer_query_skips_llm_when_context_is_empty(tmp_path):
    index_path = create_test_index(tmp_path)

    llm = RecordingLLM()

    answer = answer_query(
        query="完全无关的问题",
        index_path=index_path,
        embedder=NoMatchQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
    )

    assert answer == "资料不足，无法回答"
    assert llm.prompts == []

def create_citation_results():
    return [
        {
            "id": "docs/agent_infra.md::chunk_0001",
            "text": "沙盒负责限制文件访问范围",
            "metadata": {},
            "score": 0.98,
        },
        {
            "id": "docs/agent_infra.md::chunk_0000",
            "text": "Agent Runtime 负责循环调度",
            "metadata": {},
            "score": 0.90,
        }
    ]

def test_validate_answer_citations_accepts_valid_citations():
    answer = (
        "沙盒负责限制文件访问范围 [S2]，"
        "Agent Runtime 负责循环调度 [S1]。"
        "沙盒还能控制文件写入 [S2]。"
    )

    citations = validate_answer_citations(
        answer,
        create_citation_results(),
    )

    # 去重，但保留首次出现的顺序
    assert citations == ["S2", "S1"]

def test_validate_answer_citations_rejects_missing_citation():
    with pytest.raises(ValueError, match="缺少引用"):
        validate_answer_citations(
            "沙盒负责限制文件访问范围。",
            create_citation_results(),
        )

def test_validate_answer_citations_rejects_unknown_citation():
    with pytest.raises(ValueError, match="无效引用"):
        validate_answer_citations(
            "沙盒负责限制文件访问范围 [S3]。",
            create_citation_results(),
        )

# def test_answer_query_rejects_llm_answers_without_citation(tmp_path):
#     index_path = create_test_index(tmp_path)
#     llm = RecordingLLM(
#         response="沙盒负责限制文件访问范围。",
#     )

#     with pytest.raises(ValueError, match="缺少引用"):
#         answer_query(
#             query="什么组件负责限制文件访问？",
#             index_path=index_path,
#             embedder=FakeQueryEmbedder(),
#             llm=llm,
#             top_k=1,
#             min_score=0.5,
#         )

#     # 有检索结果，因此模型确实被调用了一次
#     assert len(llm.prompts) == 1
def test_answer_query_retries_invalid_citation_then_succeeds(tmp_path):
    index_path = create_test_index(tmp_path)
    llm = SequenceLLM(
        [
            "沙盒负责限制文件访问范围。",
            "沙盒负责限制文件访问范围 [S1]。",
        ]
    )

    answer = answer_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
        max_citation_retries=1,
    )

    assert answer == "沙盒负责限制文件访问范围 [S1]。"
    assert len(llm.prompts) == 2
    assert "引用校验失败" in llm.prompts[1]

def test_answer_query_refuses_after_citation_retries_exhausted(tmp_path):
    index_path = create_test_index(tmp_path)

    llm = SequenceLLM([
        "沙盒负责限制文件访问范围。",
        "沙盒负责限制文件访问范围 [S2]。",
    ])

    answer = answer_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
        max_citation_retries=1,
    )

    assert answer == "资料不足，无法回答"
    assert len(llm.prompts) == 2


class SequenceLLM:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        # response_index = len(self.prompts) - 1
        # return self.responses[response_index]
        try:
            return next(self._responses)
        except StopIteration as error:
            raise AssertionError(
                "LLM was called more times than expected. "
            ) from error

def test_run_rag_query_records_success_trace(tmp_path):
    index_path = create_test_index(tmp_path)
    llm = RecordingLLM()

    result = run_rag_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
    )

    assert result.answer == "沙盒负责限制文件访问范围 [S1]"

    trace = result.trace
    assert trace.query == "什么组件负责限制文件访问？"
    assert trace.retrieved_count == 1
    assert trace.top_score is not None
    assert trace.top_score >= 0.5
    assert trace.citations == ["S1"]
    assert trace.llm_calls == 1
    assert trace.citation_retries == 0
    assert trace.status == RAGStatus.SUCCESS
    assert trace.duration_seconds >= 0.0

def test_run_rag_query_records_no_context_trace(tmp_path):
    index_path = create_test_index(tmp_path)
    llm = RecordingLLM()

    result = run_rag_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=1.0,
    )

    assert result.answer == "资料不足，无法回答"

    trace = result.trace
    assert trace.retrieved_count == 0
    assert trace.top_score is None
    assert trace.citations == []
    assert trace.llm_calls == 0
    assert trace.citation_retries == 0
    assert trace.status == RAGStatus.NO_CONTEXT
    assert trace.duration_seconds >= 0.0

    assert len(llm.prompts) == 0

def test_answer_query_delegates_to_run_rag_query(monkeypatch):
    captured = {}
    fake_embedder = object()
    fake_llm = object()

    def fake_run_rag_query(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(answer="兼容层答案")

    monkeypatch.setattr(
        rag_chain_module,
        "run_rag_query",
        fake_run_rag_query,
    )

    answer = rag_chain_module.answer_query(
        query="测试问题",
        index_path="unused-index.json",
        embedder=fake_embedder,
        llm=fake_llm,
        top_k=2,
        min_score=0.6,
        max_citation_retries=3,
    )

    assert answer == "兼容层答案"
    assert captured == {
        "query": "测试问题",
        "index_path": "unused-index.json",
        "embedder": fake_embedder,
        "llm": fake_llm,
        "top_k": 2,
        "min_score": 0.6,
        "max_citation_retries": 3,
    }

def test_run_rag_query_records_retry_success_trace(tmp_path):
    index_path = create_test_index(tmp_path)
    invalid_answer = "这是一个没有合法引用的初次回答"

    llm = SequenceLLM(
        [
            invalid_answer,
            "沙盒负责限制文件访问范围 [S1]。",
        ]
    )

    result = run_rag_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
        max_citation_retries=1,
    )

    assert result.answer == "沙盒负责限制文件访问范围 [S1]。"

    trace = result.trace
    assert trace.retrieved_count == 1
    assert trace.top_score is not None
    assert trace.citations == ["S1"]
    assert trace.llm_calls == 2
    assert trace.citation_retries == 1
    assert trace.status == RAGStatus.SUCCESS
    assert trace.duration_seconds >= 0.0

    assert len(llm.prompts) == 2
    assert invalid_answer not in llm.prompts[0]
    assert invalid_answer in llm.prompts[1]

def test_run_rag_query_records_retry_exhausted_trace(tmp_path):
    index_path = create_test_index(tmp_path)

    llm = SequenceLLM(
        [
            "第一次回答没有引用",
            "第二次回答仍然没有引用",
        ]
    )

    result = run_rag_query(
        query="什么组件负责限制文件访问？",
        index_path=index_path,
        embedder=FakeQueryEmbedder(),
        llm=llm,
        top_k=1,
        min_score=0.5,
        max_citation_retries=1,
    )

    assert result.answer == "资料不足，无法回答"

    trace = result.trace
    assert trace.retrieved_count == 1
    assert trace.top_score is not None
    assert trace.citations == []
    assert trace.llm_calls == 2
    assert trace.citation_retries == 1
    assert trace.status == RAGStatus.CITATION_REFUSED
    assert trace.duration_seconds >= 0.0

    assert len(llm.prompts) == 2
