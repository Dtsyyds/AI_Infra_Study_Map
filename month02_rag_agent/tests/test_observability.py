import json
from dataclasses import asdict

from month02_rag_agent.observability import RAGStatus, RAGTrace, RAGResult

def create_success_trace() -> RAGTrace:
    return RAGTrace(
        query="什么组件负责限制文件访问？",
        retrieved_count=1,
        top_score=0.93,
        citations=["S1"],
        llm_calls=1,
        citation_retries=0,
        status=RAGStatus.SUCCESS,
        duration_seconds=0.012,
    )

def test_rag_status_values_are_stable():
    assert [status.value for status in RAGStatus] == [
        "success",
        "no_context",
        "citation_refused",
    ]

def test_rag_result_keeps_answer_and_trace():
    trace = create_success_trace()

    result = RAGResult(
        answer="沙盒负责限制文件访问范围 [S1]",
        trace=trace,
    )

    assert result.answer == "沙盒负责限制文件访问范围 [S1]"
    assert result.trace is trace
    assert result.trace.status == RAGStatus.SUCCESS

def test_rag_trace_is_json_serializable():
    trace = create_success_trace()

    json_text = json.dumps(
        asdict(trace),
        ensure_ascii=False,
    )

    payload = json.loads(json_text)

    assert payload["query"] == "什么组件负责限制文件访问？"
    assert payload["citations"] == ["S1"]
    assert payload["status"] == "success"
    assert payload["duration_seconds"] == 0.012