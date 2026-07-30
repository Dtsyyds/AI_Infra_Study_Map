import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from month02_rag_agent.indexer import build_index, save_index
from month02_rag_agent.observability import RAGResult
from month02_rag_agent.rag_chain import run_rag_query


DEMO_MODEL_NAME = "demo-static-embedding-v1"


@dataclass(frozen=True)
class ScenarioConfig:
    query: str
    query_vector: tuple[float, ...]
    llm_responses: tuple[str, ...]
    min_score: float


SCENARIOS = {
    "success": ScenarioConfig(
        query="什么组件负责限制文件访问？",
        query_vector=(0.0, 1.0),
        llm_responses=(
            "沙盒负责限制文件访问范围，防止越界读写 [S1]",
        ),
        min_score=0.8,
    ),
    "no-context": ScenarioConfig(
        query="系统如何预测明天的天气？",
        query_vector=(-1.0, 0.0),
        llm_responses=(),
        min_score=0.8,
    ),
    "citation-refused": ScenarioConfig(
        query="什么组件负责限制文件访问？",
        query_vector=(0.0, 1.0),
        llm_responses=(
            "沙盒负责限制文件访问范围。",
            "沙盒可以防止越界读写。",
        ),
        min_score=0.8,
    ),
}


class StaticEmbedder:
    """返回场景预设向量，避免加载真实 Embedding 模型。"""

    model_name = DEMO_MODEL_NAME

    def __init__(self, query_vector: tuple[float, ...]) -> None:
        self._query_vector = query_vector

    def embed_query(self, query: str) -> list[float]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")

        return list(self._query_vector)


class SequenceLLM:
    """按照顺序返回预设回答，模拟一次或多次 LLM 调用。"""

    def __init__(self, responses: tuple[str, ...]) -> None:
        self._responses = iter(responses)

    def generate(self, prompt: str) -> str:
        try:
            return next(self._responses)
        except StopIteration as error:
            raise AssertionError(
                "LLM 被调用的次数超出场景配置"
            ) from error


def create_demo_index(index_path: Path) -> None:
    index = build_index(
        chunks=[
            "Agent Runtime 负责循环调度，并记录任务执行状态。",
            "沙盒负责限制文件访问范围，防止 Agent 越界读写。",
        ],
        embeddings=[
            [1.0, 0.0],
            [0.0, 1.0],
        ],
        source="docs/demo_agent_infra.md",
        model_name=DEMO_MODEL_NAME,
        normalized=True,
    )

    save_index(index, index_path)


def run_scenario(scenario: str) -> RAGResult:
    try:
        config = SCENARIOS[scenario]
    except KeyError as error:
        raise ValueError(f"未知场景：{scenario}") from error

    with TemporaryDirectory(prefix="rag-trace-demo-") as temp_dir:
        index_path = Path(temp_dir) / "demo_index.json"
        create_demo_index(index_path)

        return run_rag_query(
            query=config.query,
            index_path=index_path,
            embedder=StaticEmbedder(config.query_vector),
            llm=SequenceLLM(config.llm_responses),
            top_k=1,
            min_score=config.min_score,
            max_citation_retries=1,
        )


def result_to_dict(
    scenario: str,
    result: RAGResult,
) -> dict:
    trace = asdict(result.trace)

    # 显式转换 Enum，避免输出 "RAGStatus.SUCCESS"
    trace["status"] = result.trace.status.value
    trace["duration_seconds"] = round(
        result.trace.duration_seconds,
        6,
    )

    return {
        "scenario": scenario,
        "answer": result.answer,
        "trace": trace,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="离线运行 RAG 并输出可观测性 Trace",
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS),
        default="success",
        help="要运行的确定性演示场景",
    )
    args = parser.parse_args()

    result = run_scenario(args.scenario)

    print(
        json.dumps(
            result_to_dict(args.scenario, result),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())