from unittest.mock import create_autospec
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from month04_deploy.app.durable.idempotency import (
    IdempotencyConflictError,
)
from month04_deploy.app.durable.routes import create_task_router
from month04_deploy.app.durable.task_store import TaskStore


REQUEST = {
    "task_type": "demo.echo",
    "payload": {"text": "hello"},
}
KEY = "api-idempotency-test"


@pytest.fixture
def api_client():
    store = create_autospec(TaskStore, instance=True)

    app = FastAPI()
    app.include_router(create_task_router(store))

    with TestClient(app) as client:
        yield client, store


def make_row(status="queued"):
    return {
        "task_id": uuid4(),
        "task_type": REQUEST["task_type"],
        "payload": REQUEST["payload"],
        "status": status,
        "result": (
            {"answer": "echo:hello"}
            if status == "succeeded"
            else None
        ),
        "error": (
            {"code": "demo_failure"}
            if status == "failed"
            else None
        ),
    }


def test_without_key_uses_original_creation(api_client):
    client, store = api_client
    store.create_task.return_value = make_row()

    response = client.post("/v1/tasks", json=REQUEST)

    assert response.status_code == 202
    store.create_task.assert_called_once_with(
        task_type="demo.echo",
        payload={"text": "hello"},
    )
    store.create_task_idempotent.assert_not_called()


@pytest.mark.parametrize(
    ("task_status", "http_status"),
    [
        ("queued", 202),
        ("running", 202),
        ("succeeded", 200),
        ("failed", 200),
        ("cancelled", 200),
    ],
)
def test_key_returns_current_task_state(
    api_client,
    task_status,
    http_status,
):
    client, store = api_client
    row = make_row(task_status)
    store.create_task_idempotent.return_value = row

    response = client.post(
        "/v1/tasks",
        json=REQUEST,
        headers={"Idempotency-Key": KEY},
    )

    assert response.status_code == http_status

    body = response.json()
    assert body["task_id"] == str(row["task_id"])
    assert body["status"] == task_status
    assert body["result"] == row["result"]
    assert body["error"] == row["error"]
    assert body["status_url"] == f"/v1/tasks/{row['task_id']}"
    assert response.headers["Location"] == body["status_url"]

    store.create_task_idempotent.assert_called_once_with(
        task_type="demo.echo",
        payload={"text": "hello"},
        idempotency_key=KEY,
    )
    store.create_task.assert_not_called()


def test_conflicting_key_returns_409(api_client):
    client, store = api_client
    store.create_task_idempotent.side_effect = (
        IdempotencyConflictError("different payload")
    )

    response = client.post(
        "/v1/tasks",
        json=REQUEST,
        headers={"Idempotency-Key": KEY},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "idempotency_conflict"}
    store.create_task.assert_not_called()


def test_database_error_returns_503(api_client):
    client, store = api_client
    store.create_task_idempotent.side_effect = (
        psycopg.OperationalError("database unavailable")
    )

    response = client.post(
        "/v1/tasks",
        json=REQUEST,
        headers={"Idempotency-Key": KEY},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "database_operation_failed",
    }
    store.create_task.assert_not_called()


@pytest.mark.parametrize("key", ["", "x" * 129])
def test_invalid_key_rejected_before_store(api_client, key):
    client, store = api_client

    response = client.post(
        "/v1/tasks",
        json=REQUEST,
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422
    store.create_task.assert_not_called()
    store.create_task_idempotent.assert_not_called()