import json
from types import SimpleNamespace

import httpx
import pytest

from config.settings import Settings
from core.errors import RuntimeFault
from runtime.context.repo_map import repo_map
from runtime.planning.adapters import ResponsesPlanner


def test_ast_dynamic_references_unresolved_and_no_execution():
    files = {"src/a.py": "import nonexistent\ndef f(x):\n    return x.run()\n"}
    result = repo_map(files, ["src/a.py"])
    assert result[0]["symbols"][0]["signature"] == "x"
    assert result[0]["references"][0]["resolution"] == "UNRESOLVED"
    assert result[0]["imports"][0]["syntax"] == "import nonexistent"


async def test_model_disabled_does_not_read_secret_or_send(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("secret read before authorization")

    config = Settings()
    monkeypatch.setattr(
        "runtime.planning.adapters.os", SimpleNamespace(environ=SimpleNamespace(get=forbidden))
    )
    with pytest.raises(RuntimeFault, match="MODEL_BLOCKED"):
        await ResponsesPlanner(config).propose({}, max_seconds=2)


@pytest.mark.parametrize("mode", ["valid", "extra", "incomplete", "refusal", "invalid", "network", "top_array", "bad_item"])
async def test_recorded_transport_contract(monkeypatch, mode):
    monkeypatch.setenv("ARC_TEST_MODEL_SECRET", "test-only-not-a-key")
    config = Settings(
        model_enabled=True,
        model_name="test-model-not-live",
        model_secret_env="ARC_TEST_MODEL_SECRET",
        model_budget_id="test-only",
        model_request_limit=1,
        model_token_limit=100000,
    )

    def handle(request):
        body = json.loads(request.content)
        assert body["text"]["format"]["strict"] is True and body["store"] is False
        assert "tools" not in body
        if mode == "network":
            raise httpx.ConnectError("synthetic")
        patch = {"edits": [{"path": "src/solution.py", "old": "a", "new": "b"}]}
        if mode == "extra":
            patch["shell"] = "rm something"
        response = {
            "id": "recorded-request",
            "model": "test-model-not-live",
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(patch)}]}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        if mode == "incomplete":
            response["status"] = "incomplete"
        if mode == "refusal":
            response["output"] = []
        if mode == "invalid":
            return httpx.Response(200, content=b"{")
        if mode == "top_array":
            return httpx.Response(200, json=[])
        if mode == "bad_item":
            response["output"] = ["bad"]
        return httpx.Response(200, json=response)

    adapter = ResponsesPlanner(config, transport=httpx.MockTransport(handle))
    if mode == "valid":
        patch, metadata = await adapter.propose({}, max_seconds=2)
        assert metadata["request_id"] == "recorded-request" and patch.edits[0].new == "b"
    else:
        with pytest.raises(RuntimeFault):
            await adapter.propose({}, max_seconds=2)


def test_summary_cannot_erase_protected_identity():
    import copy

    from runtime.context.engine import ContextEngine
    from tests.unit.test_context import DOCUMENT, RULES, RuleStore

    original = {
        "protected_facts": {"document": copy.deepcopy(DOCUMENT), "rules": copy.deepcopy(RULES)}
    }
    for _repeat in range(2):
        for mutation in (
            "amount",
            "document_version",
            "rule_version",
            "rule_source",
            "missing_document",
        ):
            changed = copy.deepcopy(original)
            facts = changed["protected_facts"]
            if mutation == "amount":
                facts["document"]["amount"] = 0
            elif mutation == "document_version":
                facts["document"]["version"] = "other"
            elif mutation == "rule_version":
                facts["rules"][0]["version"] = "other"
            elif mutation == "rule_source":
                facts["rules"][0]["source"] = "other"
            else:
                del facts["document"]
            with pytest.raises(RuntimeFault, match="summary must preserve"):
                ContextEngine(RuleStore()).apply_summary(original, changed)


async def test_repair_candidate_identity_and_pending_effects_are_protected():
    import copy
    from datetime import UTC, datetime, timedelta

    from core.contracts.execution import Execution
    from core.contracts.identity import Principal
    from core.contracts.task import TaskRequest
    from runtime.context.repair_context import RepairContext
    from runtime.repair.manifest import load_case

    manifest, base = load_case("case-01")
    ex = Execution(task_id="task", attempt_id="attempt", fence=1,
                   deadline_at=datetime.now(UTC) + timedelta(seconds=30),
                   principal=Principal(principal_id="p"),
                   request=TaskRequest(task_type="code_repair", case_id="case-01", session_id="s"),
                   context={"manifest": manifest.model_dump(), "base_files": base})
    engine = RepairContext()
    ex.context.update((await engine.build(ex)).model_dump())
    ex.context = engine.observe(ex.context, {"candidate": {"candidate_no": 1, "candidate_hash": "a"*64}})
    assert (await engine.build(ex)).protected_facts["candidate"]["candidate_no"] == 1
    valid = {"summary": "bounded note", "protected_facts": copy.deepcopy(ex.context["protected_facts"])}
    assert engine.apply_summary(ex.context, valid)["candidate"] == ex.context["candidate"]
    for field in ("candidate", "pending_effects", "manifest_hash", "deadline"):
        bad = copy.deepcopy(valid)
        bad["protected_facts"].pop(field)
        with pytest.raises(RuntimeFault, match="PROTECTED_FACTS_LOST"):
            engine.apply_summary(ex.context, bad)
    ex.context["candidate"]["candidate_no"] = 2
    with pytest.raises(RuntimeFault, match="PROTECTED_FACTS_LOST"):
        await engine.build(ex)
