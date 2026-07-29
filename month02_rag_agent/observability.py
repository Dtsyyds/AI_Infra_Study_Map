from dataclasses import dataclass
from enum import Enum

class RAGStatus(str, Enum):
    SUCCESS = "success"
    NO_CONTEXT = "no_context"
    CITATION_REFUSED = "citation_refused"

@dataclass
class RAGTrace:
    query: str
    retrieved_count: int
    top_score: float | None
    citations: list[str]
    llm_calls: int
    citation_retries: int
    status: RAGStatus
    duration_seconds: float

@dataclass
class RAGResult:
    answer: str     # 用户需要的结果
    trace: RAGTrace  # 系统运行事实 status:固定分类 计数、引用、分割、耗时
