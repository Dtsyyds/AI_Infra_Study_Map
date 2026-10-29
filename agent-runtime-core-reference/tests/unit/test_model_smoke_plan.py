import pytest

from examples.model_smoke_plan import build_plan


def test_smoke_draft_is_dev_only_and_does_not_authorize_spending(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline plan must not use transport")
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    plan = build_plan()
    assert plan["split"] == "dev" and plan["planned_tasks"] == 1
    assert plan["authorization_draft"]["approved"] is False
    assert plan["authorization_draft"]["active_request_budget"] == 0
    assert plan["real_model_tasks"] == plan["network_requests"] == 0
    assert plan["schema"]["additionalProperties"] is False


def test_smoke_rejects_holdout():
    with pytest.raises(ValueError, match="DEV_CASE"):
        build_plan("case-05")
