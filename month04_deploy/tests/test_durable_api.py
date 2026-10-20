from unittest.mock import create_autospec
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from month04_deploy.app.api import create_app
from month04_deploy.app.durable.task_store import TaskStore


@pytest.fixture
def api_client():
    store = create_autospec(TaskStore)
    app = create_app(task_store=store)

    with TestClient(app) as client:
        yield client, store


def test_submit_and_query(api_client):
    client, store = api_client

    row = {
        "task_id": uuid4(),
        "task_type": "demo.echo",
        "payload": {"text": "hello"},
        "status": "queued",
        "result": None,
        "error": None,
    }
    store.create_task.return_value = row
    store.get_task.return_value = row

    response = client.post(
        "/v1/tasks",
        json={
            "task_type": "demo.echo",
            "payload": {"text": "hello"},
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == str(row["task_id"])
    assert body["status"] == "queued"
    assert body["status_url"] == f"/v1/tasks/{row['task_id']}"
    assert response.headers["Location"] == body["status_url"]
    assert store.create_task.call_count == 1

    fetched = client.get(body["status_url"])

    assert fetched.status_code == 200
    assert fetched.json()["task_id"] == body["task_id"]
    assert fetched.json()["payload"] == {"text": "hello"}
    store.get_task.assert_called_once_with(row["task_id"])


def test_create_error_does_not_return_202(api_client):
    client, store = api_client
    store.create_task.side_effect = psycopg.OperationalError(
        "simulated commit failure"
    )

    response = client.post(
        "/v1/tasks",
        json={
            "task_type": "demo.echo",
            "payload": {"text": "hello"},
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "database_operation_failed"
    }
    assert "Location" not in response.headers


def test_missing_task_returns_404(api_client):
    client, store = api_client
    store.get_task.return_value = None

    response = client.get(f"/v1/tasks/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "task_not_found"


def test_query_error_is_not_missing_task(api_client):
    client, store = api_client
    store.get_task.side_effect = psycopg.OperationalError(
        "database unavailable"
    )

    response = client.get(f"/v1/tasks/{uuid4()}")

    assert response.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"task_type": "unknown", "payload": {"text": "hello"}},
        {"task_type": "demo.echo", "payload": {"text": ""}},
        {"task_type": "demo.echo", "payload": {}},
        {
            "task_type": "demo.echo",
            "payload": {"text": "hello"},
            "status": "succeeded",
        },
    ],
)
def test_invalid_submission_does_not_write(api_client, body):
    client, store = api_client

    response = client.post("/v1/tasks", json=body)

    assert response.status_code == 422
    store.create_task.assert_not_called()


def test_invalid_uuid_does_not_query(api_client):
    client, store = api_client

    response = client.get("/v1/tasks/not-a-uuid")

    assert response.status_code == 422
    store.get_task.assert_not_called()