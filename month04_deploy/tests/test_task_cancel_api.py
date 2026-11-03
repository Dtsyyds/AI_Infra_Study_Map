"""验证 HTTP 是否准确报告已登记的取消请求和已经结束的任务。"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from month04_deploy.app.api import create_app
from month04_deploy.app.durable.routes import create_task_router
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_cancel_request_integration import cancel_store as cancel_store
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_worker_attempts_integration import read_snapshot, run_worker, store as store


DB = pytest.mark.skipif(os.getenv("AGENT_RUN_DB_TESTS") != "1", reason="需要 PostgreSQL 集成测试")
REQUEST = {"task_type": "demo.echo", "payload": {"text": "cancel"}}
FIRST_REQUEST = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)


def client_for(store):
    app = FastAPI()
    app.include_router(create_task_router(store))
    return TestClient(app)


@pytest.fixture
def cancel_client():
    store = create_autospec(TaskStore, instance=True)
    with client_for(store) as client:
        yield client, store


@pytest.mark.parametrize(("status", "requested_at", "code"), [
    ("running", FIRST_REQUEST, 202),
    ("cancelled", FIRST_REQUEST, 200),
    ("succeeded", None, 200),
    ("failed", None, 200),
])
def test_cancel_returns_the_committed_snapshot(cancel_client, status, requested_at, code):
    client, store = cancel_client
    task_id = uuid4()
    row = {
        **REQUEST, "task_id": task_id, "status": status, "cancel_requested_at": requested_at,
        "result": {"answer": "echo:cancel"} if status == "succeeded" else None,
        "error": {"message": "original failure"} if status == "failed" else None,
    }
    store.request_task_cancel.return_value = row
    location = f"/v1/tasks/{task_id}"
    response = client.post(f"{location}/cancel")
    assert response.status_code == code
    assert response.headers["Location"] == location
    body = response.json()
    assert (body["task_id"], body["status"]) == (str(task_id), status)
    assert (body["result"], body["error"]) == (row["result"], row["error"])
    if requested_at is None:
        assert body["cancel_requested_at"] is None
    else:
        assert datetime.fromisoformat(body["cancel_requested_at"]) == requested_at
    store.request_task_cancel.assert_called_once_with(task_id=task_id)
    store.get_task.assert_not_called()


def test_missing_task_returns_404(cancel_client):
    client, store = cancel_client
    store.request_task_cancel.return_value = None
    response = client.post(f"/v1/tasks/{uuid4()}/cancel")
    assert response.status_code == 404 and response.json()["detail"] == "task_not_found"
    assert "Location" not in response.headers


def test_invalid_uuid_does_not_reach_store(cancel_client):
    client, store = cancel_client
    response = client.post("/v1/tasks/not-a-uuid/cancel")
    assert response.status_code == 422
    store.request_task_cancel.assert_not_called()


def test_unconfigured_store_returns_503():
    with client_for(None) as client:
        response = client.post(f"/v1/tasks/{uuid4()}/cancel")
    assert response.status_code == 503 and response.json()["detail"] == "task_store_not_configured"
    assert "Location" not in response.headers


def test_database_error_does_not_acknowledge_cancellation(cancel_client):
    client, store = cancel_client
    store.request_task_cancel.side_effect = psycopg.OperationalError("commit unavailable")
    response = client.post(f"/v1/tasks/{uuid4()}/cancel")
    assert response.status_code == 503 and response.json()["detail"] == "database_operation_failed"
    assert "Location" not in response.headers


@pytest.mark.parametrize("status", ["queued", "running"])
def test_nonterminal_row_without_registration_does_not_acknowledge_cancel(cancel_client, status):
    client, store = cancel_client
    task_id = uuid4()
    store.request_task_cancel.return_value = {**REQUEST, "task_id": task_id, "status": status}
    response = client.post(f"/v1/tasks/{task_id}/cancel")
    assert response.status_code == 500 and response.json()["detail"] == "invalid_cancel_state"
    assert "Location" not in response.headers


@DB
def test_cancel_queued_task_over_http_returns_terminal_view(cancel_store):
    store = cancel_store
    with TestClient(create_app(task_store=store)) as client:
        submitted = client.post("/v1/tasks", json=REQUEST)
        assert submitted.status_code == 202
        location = submitted.headers["Location"]
        task_id = UUID(submitted.json()["task_id"])
        assert client.get(location).json()["cancel_requested_at"] is None
        first = client.post(f"{location}/cancel")
        assert first.status_code == 200 and first.headers["Location"] == location
        assert first.json()["status"] == "cancelled" and first.json()["cancel_requested_at"] is not None
        again = client.post(f"{location}/cancel")
        view = client.get(location)
        assert again.status_code == view.status_code == 200
        assert again.json() == view.json() == first.json()
    task, attempts, steps = read_snapshot(store, task_id)
    assert task["cancel_requested_at"] == datetime.fromisoformat(first.json()["cancel_requested_at"])
    assert attempts == steps == []


@DB
def test_http_replay_across_worker_confirmation_preserves_first_time(cancel_store):
    store = cancel_store
    with TestClient(create_app(task_store=store)) as client:
        submitted = client.post("/v1/tasks", json=REQUEST)
        assert submitted.status_code == 202
        location = submitted.headers["Location"]
        task_id = UUID(submitted.json()["task_id"])
        task, attempt = store.try_start_task_with_attempt(task_id)
        set_lease_remaining(store, task, timedelta(hours=1))
        saved = store.try_save_completed_step(
            task_id, 0, "read_input_v1", REQUEST["payload"], expected_lease_version=task["lease_version"],
            expected_attempt_id=attempt["attempt_id"],
        )
        assert isinstance(saved, dict)
        first = client.post(f"{location}/cancel")
        assert first.status_code == 202 and first.headers["Location"] == location
        assert first.json()["status"] == "running"
        original_time = first.json()["cancel_requested_at"]
        assert original_time is not None

    with TestClient(create_app(task_store=store)) as client:
        replay = client.post(f"{location}/cancel")
        pending = client.get(location)
        assert replay.status_code == 202 and pending.status_code == 200
        assert replay.json() == pending.json() == first.json()
        _, attempts, steps = read_snapshot(store, task_id)
        assert attempts == [attempt] and steps == [saved]

        process = run_worker(store, task_id, "--resume")
        assert process.returncode == 0 and "CANCELLED" in process.stdout.splitlines(), process.stderr
        finished = client.get(location)
        replay = client.post(f"{location}/cancel")
        assert finished.status_code == replay.status_code == 200
        assert replay.headers["Location"] == location and replay.json() == finished.json()
        assert finished.json()["status"] == "cancelled"
        assert finished.json()["cancel_requested_at"] == original_time
    task, attempts, steps = read_snapshot(store, task_id)
    assert task["cancel_requested_at"] == datetime.fromisoformat(original_time)
    assert [(a["attempt_id"], a["status"]) for a in attempts] == [(attempt["attempt_id"], "interrupted")]
    assert steps == [saved]
