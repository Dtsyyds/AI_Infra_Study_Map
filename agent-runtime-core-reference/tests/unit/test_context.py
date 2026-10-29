import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.contracts.execution import Execution
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.errors import RuntimeFault
from runtime.context.engine import ContextEngine, estimated_tokens, validate_citations

RULES: list[dict[str, Any]] = [
    {
        "rule_id": "RULE-APPROVAL",
        "version": "1",
        "source": "fictional-policy#approval",
        "rule_type": "approval_threshold",
        "threshold": 10000,
        "text": "虚构规则：超过 10000 需审批附件。",
    },
    {
        "rule_id": "RULE-INVOICE",
        "version": "1",
        "source": "fictional-policy#invoice",
        "rule_type": "invoice_required",
        "text": "虚构规则：必须有发票。",
    },
]
DOCUMENT = {
    "document_id": "DOC-001",
    "version": "1",
    "amount": 12000,
    "attachments": ["invoice"],
    "source": "fictional-documents#DOC-001",
}


class RuleStore:
    async def rules(self) -> list[dict[str, Any]]:
        return copy.deepcopy(RULES)


def execution(context: dict[str, Any] | None = None, version: int = 0) -> Execution:
    return Execution(
        task_id="task-context",
        attempt_id="attempt-context",
        fence=1,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        principal=Principal(principal_id="demo-reviewer"),
        request=TaskRequest(document_id="DOC-001", session_id="context-test"),
        context=context or {},
        session_version=version,
    )


@pytest.mark.asyncio
async def test_long_history_compacts_preserving_facts_and_rebuilds():
    engine = ContextEngine(RuleStore(), max_chars=5000, keep_recent=3)
    context = (await engine.build(execution())).model_dump()
    context = engine.observe(context, {"document": DOCUMENT})
    for index in range(100):
        context = engine.observe(context, {"note": f"history-{index}:" + "观察" * 100})
    assert len(context["working_memory"]) <= 3
    assert "确定性历史摘要" in context["summary"]
    assert context["protected_facts"] == {"rules": RULES, "document": DOCUMENT}
    rebuilt = await engine.build(execution(context))
    assert rebuilt.protected_facts == context["protected_facts"]
    assert rebuilt.summary == context["summary"]
    assert rebuilt.estimated_tokens > 0


@pytest.mark.asyncio
async def test_bad_summary_is_rejected_and_original_is_unchanged():
    engine = ContextEngine(RuleStore())
    context = engine.observe((await engine.build(execution())).model_dump(), {"document": DOCUMENT})
    candidate = copy.deepcopy(context)
    del candidate["protected_facts"]["document"]["amount"]
    with pytest.raises(RuntimeFault, match="summary must preserve"):
        engine.apply_summary(context, candidate)
    assert context["protected_facts"]["document"]["amount"] == 12000


@pytest.mark.asyncio
async def test_session_version_and_document_conflicts_are_rejected():
    engine = ContextEngine(RuleStore())
    context = (await engine.build(execution())).model_dump()
    with pytest.raises(RuntimeFault, match="checkpoint/session"):
        await engine.build(execution(context, version=1))
    context = engine.observe(context, {"document": DOCUMENT})
    with pytest.raises(RuntimeFault, match="summary must preserve"):
        engine.observe(context, {"document": {**DOCUMENT, "amount": 1}})


@pytest.mark.asyncio
async def test_old_rule_version_survives_checkpoint_rebuild():
    engine = ContextEngine(RuleStore())
    context = (await engine.build(execution())).model_dump()
    context["rules"][0]["version"] = "snapshot-version"
    context["protected_facts"]["rules"][0]["version"] = "snapshot-version"
    rebuilt = await engine.build(execution(context))
    assert rebuilt.rules[0]["version"] == "snapshot-version"


def test_citations_validate_identity_version_and_source():
    valid = [{key: rule[key] for key in ("rule_id", "version", "source")} for rule in RULES]
    validate_citations({"citations": valid}, RULES)
    for invalid in [None, [], valid[:1], [{**valid[0], "version": "2"}, valid[1]]]:
        with pytest.raises(RuntimeFault):
            validate_citations({"citations": invalid}, RULES)


@pytest.mark.asyncio
async def test_no_silent_truncation_when_protected_document_exceeds_budget():
    engine = ContextEngine(RuleStore(), max_chars=3000)
    context = (await engine.build(execution())).model_dump()
    with pytest.raises(RuntimeFault, match="protected facts exceed"):
        engine.observe(context, {"document": {**DOCUMENT, "content": "巨大" * 5000}})


def test_estimator_is_explicit_character_heuristic():
    assert estimated_tokens("123456") == 2
