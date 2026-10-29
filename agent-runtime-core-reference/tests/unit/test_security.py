import json

import pytest
from pydantic import SecretStr

from config.settings import Settings
from core.contracts.identity import Principal
from core.errors import RuntimeFault
from runtime.security import (
    authenticate,
    authorize,
    authorize_reconcile,
    guard_input,
    guard_output,
    principal_from_token,
    redact,
)


def test_verified_credentials_choose_identity_and_role():
    config = Settings(
        user_token=SecretStr("test-review"),
        restricted_token=SecretStr("test-restricted"),
        ops_token=SecretStr("test-ops"),
    )
    assert principal_from_token("test-review", config).role == "reviewer"
    assert principal_from_token("test-restricted", config).role == "restricted"
    assert principal_from_token("test-ops", config).role == "operator"
    with pytest.raises(RuntimeFault, match="invalid local"):
        principal_from_token("unknown", config)
    with pytest.raises(RuntimeFault, match="Bearer credential"):
        authenticate(None)


def test_static_policy_checks_identity_role_scope_task_and_tool():
    reviewer = Principal(principal_id="demo-reviewer")
    authorize(reviewer, "document.fetch", "document_review")
    for principal, tool, task_type in [
        (Principal(principal_id="demo-restricted", role="restricted"), "document.fetch", "document_review"),
        (Principal(principal_id="demo-operator", role="operator"), "report.publish", "document_review"),
        (Principal(principal_id="forged-reviewer"), "document.fetch", "document_review"),
        (reviewer.model_copy(update={"scope": "another-tenant"}), "document.fetch", "document_review"),
        (reviewer, "shell.exec", "document_review"),
        (reviewer, "document.fetch", "another-task"),
    ]:
        with pytest.raises(RuntimeFault) as error:
            authorize(principal, tool, task_type)
        assert error.value.code == "TOOL_FORBIDDEN"


def test_reconcile_requires_independent_operator_identity():
    authorize_reconcile(Principal(principal_id="demo-operator", role="operator"))
    with pytest.raises(RuntimeFault):
        authorize_reconcile(Principal(principal_id="demo-reviewer"))


def test_three_redaction_boundaries_remove_nested_secret_and_free_text():
    original = {
        "document_id": "DOC-001",
        "api_key": "private-example-value",
        "nested": [{"text": "returned SIMULATED_SECRET_DO_NOT_LEAK"}],
        "message": "Bearer local-demo-reviewer-not-a-secret",
    }
    for boundary in (guard_input, redact, guard_output):
        clean = boundary(original)
        serialized = json.dumps(clean)
        assert "private-example-value" not in serialized
        assert "SIMULATED_SECRET_DO_NOT_LEAK" not in serialized
        assert "local-demo-reviewer-not-a-secret" not in serialized
        assert clean["document_id"] == "DOC-001"
    assert original["api_key"] == "private-example-value"
