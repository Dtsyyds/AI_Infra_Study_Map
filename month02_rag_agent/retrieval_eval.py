from collections.abc import Sequence
from month02_rag_agent.retriever import search
import json
from pathlib import Path
from month02_rag_agent.indexer import load_index
import os
import tempfile
import math
import argparse
import sys
from month02_rag_agent.embedder import LocalEmbedder

def recall_at_k(
        retrieved_ids: Sequence[str],
        relevant_ids: set[str],
        *,
        k: int,
) -> float:
    """
    计算前 k 检索结果覆盖了多少相关文档
    """
    if k <= 0:
        raise ValueError("k 必须大于 0")

    # if not relevant_ids:
    #     raise ValueError("relevant_ids 不能为空")

    # aim_nums = 0
    # for ith_id in retrieved_ids[:k]:
    #     if ith_id in relevant_ids:
    #         aim_nums += 1

    top_k_ids = set(retrieved_ids[:k])  # 使用集合对前 k 个检索结果进行去重
    match_ids = top_k_ids & relevant_ids

    return len(match_ids) / len(relevant_ids)

def reciprocal_rank(
        retrieved_ids: Sequence[str],
        relevant_ids: set[str],
) -> float:
    """
    返回第一个相关结果排名的倒数,完全未命中返回 0
    """
    if not relevant_ids:
        raise ValueError("relevant_ids 不能为空")

    # for ith_id in retrieved_ids:
    #     if ith_id in relevant_ids:
    #         return 1 / (retrieved_ids.index(ith_id) + 1)
    # 每次使用 index() 都会重新从头查找。直接用 enumerate() 更准确地表达“排名”

    for rank, retrieved_id in enumerate(
        retrieved_ids,
        start=1,
    ):
        if retrieved_id in relevant_ids:
            return 1.0 / rank

    return 0

RankingCase = tuple[
    Sequence[str],
    set[str],
]

def evaluate_rankings(
        cases: Sequence[RankingCase],
        *,
        k: int,
) -> dict[str, int | float]:
    if not cases:
        raise ValueError("cases 不能为空")

    recall_score = 0.0
    reciprocal_score = 0.0

    # for case in cases:
    #     recall_score += recall_at_k(case[0], case[1], k=k)
    #     reciprocal_score += reciprocal_rank(case[0], case[1])

    for retrieved_ids, relevant_ids in cases:
        recall_score += recall_at_k(
            retrieved_ids,
            relevant_ids,
            k=k,
        )
        reciprocal_score += reciprocal_rank(
            retrieved_ids,
            relevant_ids,
        )

    recall_score = recall_score / len(cases)
    mrr = reciprocal_score / len(cases)

    return {
        "query_count": len(cases),
        "k": k,
        "recall_at_k": recall_score,
        "mrr": mrr,
    }

def evaluate_retriever(
        cases: Sequence[dict],
        records: list[dict],
        embedder,
        *,
        top_k: int,
) -> dict[str, object]:
    """
    执行检索评测，同时返回汇总指标和逐用例结果。
    """
    if not cases:
        raise ValueError("cases 不能为空")

    rankings = []
    case_results = []

    for case in cases:
        query_vector = embedder.embed_query(case["query"])
        search_results = search(query_vector=query_vector, records=records, top_k=top_k,)
        retrieved_ids = [record["id"] for record in search_results]
        relevant_ids = set(case["relevant_ids"])

        rankings.append((retrieved_ids, relevant_ids))

        recall_score = recall_at_k(retrieved_ids, relevant_ids, k=top_k)
        rr_score = reciprocal_rank(retrieved_ids, relevant_ids)

        case_results.append({
            "id": case["id"],
            "retrieved_ids": retrieved_ids,
            "relevant_ids": list(case["relevant_ids"]),
            "recall_at_k": recall_score,
            "rr": rr_score,
        })

    summary = evaluate_rankings(cases=rankings, k=top_k)

    return {
        "summary": summary,
        "case_results": case_results,
    }

def load_eval_dataset(
        path: str|Path,
) -> dict[str, object]:
    """
    加载并校验版本化的检索评测集。
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        dataset = json.load(f)

    """
    根节点必须是字典。
    schema_version 必须严格等于整数 1。
    cases 必须是非空列表。
    每个 case 必须是字典。
    id 和 query 必须是非空字符串。
    relevant_ids 必须是非空列表。
    relevant_ids 中每一项必须是非空字符串。
    case 的 id 不能重复。
    校验通过后返回完整 dataset。
    """
    if not isinstance(dataset, dict):
        raise ValueError("根节点必须是字典")

    schema_version = dataset.get("schema_version")
    # if dataset.get("schema_version") != 1:
    #     raise ValueError("schema_version 必须等于整数 1")
    if (
        type(schema_version) is not int
        or schema_version != 1
    ):
        raise ValueError(
            "schema_version 必须等于整数 1"
        )

    # if not isinstance(dataset["cases"], list):
    #     raise ValueError("cases 必须是非空列表")
    # if not dataset["cases"]:
    #     raise ValueError("cases 不能为空")

    # seen_case_ids = set()
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 必须是非空列表")

    seen_case_ids = set()

    for index, case in enumerate(dataset["cases"]):
        if not isinstance(case, dict):
            raise ValueError(
                f"cases[{index}] 必须是字典"
            )

        case_id = case.get("id")
        query = case.get("query")
        relevant_ids = case.get("relevant_ids")

        if (
            not isinstance(case_id, str)
            or not case_id.strip()
        ):
            raise ValueError(
                f"cases[{index}].id 必须是非空字符串"
            )

        if (
            not isinstance(query, str)
            or not query.strip()
        ):
            raise ValueError(
                f"cases[{index}].query 必须是非空字符串"
            )

        if (
            not isinstance(relevant_ids, list)
            or not relevant_ids
        ):
            raise ValueError(
                f"cases[{index}].relevant_ids 必须是非空列表"
            )

        if case_id in seen_case_ids:
            raise ValueError(
                f"评测用例 id 重复：{case_id}"
            )

        if any(
            not isinstance(relevant_id, str)
            or not relevant_id.strip()
            for relevant_id in relevant_ids
        ):
            raise ValueError(
                f"cases[{index}].relevant_ids "
                "必须只包含非空字符串"
            )

        seen_case_ids.add(case_id)

    return dataset

def run_retrieval_eval(
        eval_path: str|Path,
        index_path: str|Path,
        embedder,
        *,
        top_k: int,
) -> dict[str, object]:
    dataset = load_eval_dataset(eval_path)
    index = load_index(index_path)

    embedder_model = getattr(
        embedder,
        "model_name",
        None,
    )

    if embedder_model != index["model"]:
        raise ValueError(
            "Embedding 模型不一致："
            f"index={index['model']!r}, "
            f"query={embedder_model!r}"
        )

    evaluation = evaluate_retriever(
        cases=dataset["cases"],
        records=index["records"],
        embedder=embedder,
        top_k=top_k,
    )

    return {
        "schema_version": 1,
        "index": {
            "model": index["model"],
            "dimension": index["dimension"],
            "record_count": len(index["records"]),
        },
        "summary": evaluation["summary"],
        "case_results": evaluation["case_results"],
    }

def save_eval_report(report: dict[str, object], output_path: str|Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = None

    try:
        """
        这里不能直接打开目标文件覆盖：
        with path.open("w") as file:
        因为进程在写到一半时崩溃，旧报告已经被截断，新报告又不完整。原子替换保证目标路径要么保留旧文件，要么得到完整新文件。
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(
                report,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

def _validate_rate(value, *, name: str) -> float:
    if(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise ValueError(
            f"{name} 必须是数字"
        )

    rate = float(value)

    if (
        not math.isfinite(rate)
        or rate < 0.0
        or rate > 1.0
    ):
        raise ValueError(
            f"{name} 必须是 [0.0, 1.0] 的数字"
        )

    return rate

def determine_retrieval_exit_code(
        report: dict[str, object],
        min_recall_at_k: float,
        min_mrr: float,
) -> int:
    summary = report.get("summary")

    if not isinstance(summary, dict):
        raise ValueError(
            "report.summary 必须是字典"
        )

    recall_score = _validate_rate(summary.get("recall_at_k"), name="summary.recall_at_k")
    mrr_score = _validate_rate(summary.get("mrr"), name="summary.mrr")

    recall_threshold = _validate_rate(min_recall_at_k, name="min_recall_at_k")
    mrr_threshold = _validate_rate(min_mrr, name="min_mrr")

    if (
        recall_score < recall_threshold
        or mrr_score < mrr_threshold
    ):
        return 1

    return 0

def main(
        argv: list[str] | None = None,
        *,
        embedder_factory=LocalEmbedder,
) -> int:
    parser = argparse.ArgumentParser(
        description="运行 RAG 检索质量评测"
    )

    parser.add_argument("eval_path")
    parser.add_argument("index_path")
    parser.add_argument("output_path")

    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--min-recall-at-k",
        type=float,
        default=0.8,
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=0.7,
    )

    args = parser.parse_args(argv)

    try:
        embedder = embedder_factory()

        report = run_retrieval_eval(
            eval_path=args.eval_path,
            index_path=args.index_path,
            embedder=embedder,
            top_k=args.top_k,
        )

        save_eval_report(report, args.output_path)
        exit_code = determine_retrieval_exit_code(
            report,
            min_recall_at_k=args.min_recall_at_k,
            min_mrr=args.min_mrr,
        )

        # 把 report["summary"] 这个字典格式化成一个多行、缩进的 JSON 字符串，然后打印到终端。
        print(
            json.dumps(
                report["summary"],
                ensure_ascii=False,
                indent=2,
            )
        )

        return exit_code

    except Exception as error:
        print(
            f"检索评测执行失败：{error}",
            file=sys.stderr,
        )
        return 2

if __name__ == "__main__":
    raise SystemExit(main())