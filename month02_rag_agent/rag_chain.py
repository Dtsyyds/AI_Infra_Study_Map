from pathlib import Path
import re
from time import perf_counter

from month02_rag_agent.indexer import load_index
from month02_rag_agent.retriever import search
from month02_rag_agent.observability import RAGStatus, RAGTrace, RAGResult

class CitationValidationError(ValueError):
    """模型回答违反引用协议"""

def retrieve_context(
        query: str,
        index_path: str | Path,
        embedder,
        *,
        top_k: int = 3,
        min_score: float | None = None,

) -> list[dict]:
    """
    根据查询向量，在记录中搜索最相似的 top_k 条记录
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")

    index = load_index(index_path)
    embedder_model = getattr(embedder, "model_name", None)  # embedder_model = embedder.model_name
    # 无论该属性是否存在，程序都能平稳运行，embedder_model 要么是实际的模型名称字符串，要么是 None

    if embedder_model != index["model"]:
        raise ValueError(
            f"Embedding 模型不一致："
            f"index={index['model']}, query={embedder_model!r}"
        )

    query_vector = embedder.embed_query(query)

    if len(query_vector) != index["dimension"]:
        raise ValueError("查询向量与索引维度不一致")

    return search(
        query_vector,
        index["records"],
        top_k=top_k,
        min_score=min_score,

    )

def format_context(results: list[dict]) -> str:
    """将检索结果格式化为带引用编号的上下文"""
    if not results:
        return ""

    blocks = []

    for rank, result in enumerate(results, start=1):
        block = (
            f"[S{rank}]\n"
            f"id: {result['id']!s}\n"
            f"content: {result['text']!s}"
        )
        blocks.append(block)

    return "\n\n".join(blocks)

def build_rag_prompt(
    query: str,
    results: list[dict],
) -> str:
    """将用户问题和检索证据构成受约束的 RAG Prompt"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")

    context = format_context(results)

    if not context:
        raise ValueError("检索结果为空，无法构造 RAG Prompt")

    return (
        "你是一个严格基于资料回答问题的助手。\n"
        "规则：\n"
        "1. 只能依据检索资料回答，不得补充资料之外的事实。\n"
        "2. 每个关键结论后必须使用 [S1]、[S2] 形式标注引用。\n"
        "3. 如果资料不足，回答“资料不足，无法回答”。\n\n"
        f"问题：\n{query}\n\n"
        f"检索资料：\n{context}\n\n"
        "回答："
    )

def answer_query(
    query: str,
    index_path: str | Path,
    embedder,
    llm,
    *,
    top_k: int = 3,
    min_score: float | None = None,
    max_citation_retries: int = 1,
) -> str:
    # """完成检索, Prompt 构造和回答生成"""
    # results = retrieve_context(
    #     query=query,
    #     index_path=index_path,
    #     embedder=embedder,
    #     top_k=top_k,
    #     min_score=min_score,
    # )

    # if not results:
    #     return "资料不足，无法回答"

    # base_prompt = build_rag_prompt(
    #     query=query,
    #     results=results,
    # )

    # current_prompt = base_prompt

    # for attempt in range(max_citation_retries + 1):
    #     answer = llm.generate(current_prompt)

    #     try:
    #         validate_answer_citations(
    #             answer=answer,
    #             results=results,
    #         )
    #         return answer

    #     except CitationValidationError as error:
    #         if attempt == max_citation_retries:
    #             return "资料不足，无法回答"

    #         current_prompt = (
    #         f"{base_prompt}\n\n"
    #         f"上一次回答：\n{answer}\n\n"
    #         f"引用校验失败：{error}\n"
    #         "请重新回答。只能使用检索资料中实际存在的 "
    #         "[S1]、[S2] 形式引用，并确保结论带有引用。\n\n"
    #         "修正后的回答："
    #     )

    # return answer
    result = run_rag_query(
        query=query,
        index_path=index_path,
        embedder=embedder,
        llm=llm,
        top_k=top_k,
        min_score=min_score,
        max_citation_retries=max_citation_retries,
    )
    return result.answer

def validate_answer_citations(
    answer: str,
    results: list[dict],
) -> list[str]:
    """校验回答中的引用，并返回去重后的引用标签"""
    if not isinstance(answer, str) or not answer.strip():
        raise CitationValidationError("answer 必须是非空字符串")

    if not results:
        raise ValueError("results 必须是非空列表")

    matches = re.findall(r"\[S(\d+)\]", answer)

    if not matches:
        raise CitationValidationError("回答缺少引用")

    citations = []

    for raw_rank in matches:
        rank = int(raw_rank)

        if rank < 1 or rank > len(results):
            raise CitationValidationError(f"回答包含无效引用：[S{rank}]")

        label = f"S{rank}"

        if label not in citations:
            citations.append(label)

    return citations

def run_rag_query(
    query: str,
    index_path: str | Path,
    embedder,
    llm,
    *,
    top_k: int = 3,
    min_score: float | None = None,
    max_citation_retries: int = 1,
) -> RAGResult:
    started_at = perf_counter()

    results = retrieve_context(
        query=query,
        index_path=index_path,
        embedder=embedder,
        top_k=top_k,
        min_score=min_score,
    )

    retrieve_count = len(results)
    top_score = max(
        (float(result["score"]) for result in results),
        default=None,
    )

    llm_calls = 0
    citation_retries = 0

    def finish(
        answer: str,
        status: RAGStatus,
        citations: list[str],
    ) -> RAGResult:
        return RAGResult(
            answer=answer,
            trace=RAGTrace(
                query=query,
                retrieved_count=retrieve_count,
                top_score=top_score,
                citations=citations,
                llm_calls=llm_calls,
                citation_retries=citation_retries,
                status=status,
                duration_seconds=(perf_counter() - started_at),
            ),
        )

    if not results:
        return finish(
            answer="资料不足，无法回答",
            status=RAGStatus.NO_CONTEXT,
            citations=[],
        )

    base_prompt = build_rag_prompt(
        query=query,
        results=results,
    )
    current_prompt = base_prompt

    for attempt in range(max_citation_retries + 1):
        answer = llm.generate(current_prompt)
        llm_calls +=1

        try:
            validate_answer_citations(
                answer=answer,
                results=results,
            )
        except CitationValidationError as error:
            if attempt == max_citation_retries:
                return finish(
                    answer="资料不足，无法回答",
                    status=RAGStatus.CITATION_REFUSED,
                    citations=[],
                )
            citation_retries += 1
            current_prompt = (
                f"{base_prompt}\n\n"
                f"上一次回答：\n{answer}\n\n"
                f"引用校验失败：{error}\n"
                "请重新回答。只能使用检索资料中实际存在的 "
                "[S1]、[S2] 形式引用，并确保结论带有引用。\n\n"
                "修正后的回答："
            )
            continue

        return finish(
            answer=answer,
            status=RAGStatus.SUCCESS,
            citations=extract_answer_citations(answer),
        )

    raise RuntimeError("RAG execution reached an unexpected state")

def extract_answer_citations(answer: str) -> list[str]:
    citations = []
    seen = set()

    pattern = r"\[S(\d+)\]"
    matches = re.findall(pattern, answer)

    for rank_text in matches:
        rank = int(rank_text)
        citation = f"S{rank}"

        if citation not in seen:
            citations.append(citation)
            seen.add(citation)

    return citations
