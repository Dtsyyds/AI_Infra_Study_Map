"""L4：从持久快照重建，确定性压缩工作记忆，完整保留受保护事实。"""

import copy
import hashlib
import json
from typing import Any, Protocol

from core.contracts.context import ContextBundle
from core.contracts.execution import Execution
from core.errors import RuntimeFault


class RuleStore(Protocol):
    async def rules(self) -> list[dict[str, Any]]: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def estimated_tokens(value: Any) -> int:
    """仅用字符数 / 4 估算；不声称是真实模型 tokenizer 计数。"""
    return (len(_json(value)) + 3) // 4


def validate_citations(report: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    expected = {
        (str(rule["rule_id"]), str(rule["version"]), str(rule["source"])) for rule in rules
    }
    citations = report.get("citations")
    if not isinstance(citations, list):
        raise RuntimeFault("INVALID_CITATIONS", "report citations must be a list")
    actual: set[tuple[str, str, str]] = set()
    for citation in citations:
        if not isinstance(citation, dict) or not all(
            key in citation for key in ("rule_id", "version", "source")
        ):
            raise RuntimeFault("INVALID_CITATIONS", "citation lacks stable identity")
        actual.add(
            (str(citation["rule_id"]), str(citation["version"]), str(citation["source"]))
        )
    if actual != expected:
        raise RuntimeFault("INVALID_CITATIONS", "citation set differs from retrieved rule set")


def validate_protected_facts(
    expected: dict[str, Any], candidate: dict[str, Any]
) -> None:
    if _json(expected) != _json(candidate):
        raise RuntimeFault("PROTECTED_FACTS_LOST", "summary must preserve all protected facts")


class ContextEngine:
    def __init__(self, store: RuleStore, max_chars: int = 16000, keep_recent: int = 4) -> None:
        self.store = store
        self.max_chars = max_chars
        self.keep_recent = keep_recent

    async def build(self, execution: Execution) -> ContextBundle:
        snapshot = copy.deepcopy(execution.context)
        if snapshot and snapshot.get("session_version", execution.session_version) != execution.session_version:
            raise RuntimeFault("SESSION_VERSION_CONFLICT", "checkpoint/session versions differ")
        retrieved = await self.store.rules()
        # 恢复继续使用 checkpoint 中的已检索规则版本，不悄悄换成更新后的规则。
        rules = snapshot.get("rules", retrieved)
        if not rules:
            raise RuntimeFault("RULES_MISSING", "no review rules found")
        facts = snapshot.get("protected_facts", {})
        if "rules" in facts:
            validate_protected_facts({"rules": rules}, {"rules": facts["rules"]})
        facts["rules"] = copy.deepcopy(rules)
        if "document" in snapshot:
            if "document" in facts:
                validate_protected_facts(
                    {"document": snapshot["document"]}, {"document": facts["document"]}
                )
            facts["document"] = copy.deepcopy(snapshot["document"])
        snapshot.update(
            rules=rules, protected_facts=facts, session_version=execution.session_version
        )
        bounded = self._compact(snapshot)
        return ContextBundle(
            rules=rules,
            protected_facts=facts,
            working_memory=bounded.get("working_memory", []),
            summary=bounded.get("summary", ""),
            estimated_tokens=estimated_tokens(bounded),
            session_version=execution.session_version,
        )

    def observe(self, context: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        candidate = copy.deepcopy(context)
        facts = candidate.setdefault("protected_facts", {})
        for key, value in observation.items():
            # 工具不能通过 observation 伪造规则、摘要、session 版本或覆盖受保护事实。
            if key in {"protected_facts", "rules", "session_version", "working_memory", "summary"}:
                raise RuntimeFault("CONTEXT_RESERVED_FIELD", key)
            candidate[key] = copy.deepcopy(value)
        if "document" in observation:
            if "document" in facts:
                validate_protected_facts(
                    {"document": facts["document"]}, {"document": observation["document"]}
                )
            facts["document"] = copy.deepcopy(observation["document"])
        if "report" in observation:
            validate_citations(observation["report"], candidate.get("rules", []))
        candidate.setdefault("working_memory", []).append(copy.deepcopy(observation))
        return self._compact(candidate)

    def apply_summary(
        self, context: dict[str, Any], candidate: dict[str, Any]
    ) -> dict[str, Any]:
        """用于教学验证错误摘要：任何关键事实丢失都拒绝写回。"""
        validate_protected_facts(
            context.get("protected_facts", {}), candidate.get("protected_facts", {})
        )
        return self._compact(copy.deepcopy(candidate))

    def _compact(self, context: dict[str, Any]) -> dict[str, Any]:
        memory = context.setdefault("working_memory", [])
        removed: list[dict[str, Any]] = []
        while len(memory) > self.keep_recent:
            removed.append(memory.pop(0))
        # 概要只记历史摘要，不承担事实源职责；受保护事实独立保存。
        while memory and len(_json(context)) > self.max_chars:
            removed.append(memory.pop(0))
        if removed:
            previous = context.get("summary", "")
            digest = hashlib.sha256(_json([previous, removed]).encode()).hexdigest()[:16]
            context["summary"] = (
                f"确定性历史摘要：本轮压缩 {len(removed)} 条观察；历史摘要指纹 {digest}；"
                "规则与文档事实完整保留于 protected_facts。"
            )
        if len(_json(context)) > self.max_chars:
            raise RuntimeFault("CONTEXT_LIMIT", "protected facts exceed context budget")
        context["estimated_tokens"] = estimated_tokens(context)
        return context
