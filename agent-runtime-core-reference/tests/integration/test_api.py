import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from api.main import app, validate_event_id
from config.settings import settings

pytestmark = pytest.mark.integration


def headers(key=None):
    result = {"Authorization": f"Bearer {settings.user_token.get_secret_value()}"}
    if key:
        result["Idempotency-Key"] = key
    return result


async def test_api_202_idempotency_and_sse_validation():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://reference") as client:
        body = {"document_id": "DOC-001", "session_id": str(uuid4()), "budget_ms": 10000}
        key = str(uuid4())
        response = await client.post("/tasks", json=body, headers=headers(key))
        assert response.status_code == 202
        task = response.json()
        again = await client.post("/tasks", json=body, headers=headers(key))
        assert again.json()["task_id"] == task["task_id"]
        changed = await client.post("/tasks", json={**body, "document_id": "DOC-002"}, headers=headers(key))
        assert changed.status_code == 409
        for event_id in ["bad", "-1", str(task["version"] + 1)]:
            invalid = await client.get(f"/tasks/{task['task_id']}/events", headers={**headers(), "Last-Event-ID": event_id})
            assert invalid.status_code == 400
        await client.post(f"/tasks/{task['task_id']}/cancel", headers=headers())
        snapshot = await client.get(f"/tasks/{task['task_id']}", headers=headers())
        sse = await client.get(f"/tasks/{task['task_id']}/events", headers={**headers(), "Last-Event-ID": "0"})
        data = json.loads(sse.text.split("data: ", 1)[1].strip())
        assert data["version"] == snapshot.json()["version"]
        assert data["status"] == "CANCELLED"
        assert data["reconciled"] is True
        assert sse.text.count("event: snapshot") == 1


def test_last_event_id_rejects_future():
    assert validate_event_id(None, 3) is None
    assert validate_event_id("2", 3) == 2
    for item in ["-1", "4", "abc", "1.0", "99999999999999999999"]:
        with pytest.raises(HTTPException) as exc:
            validate_event_id(item, 3)
        assert exc.value.status_code == 400


async def test_metrics_read_persisted_worker_counts():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://reference") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "arc_reference_persisted_tasks" in response.text
    assert "task_id=" not in response.text
