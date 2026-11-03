"""验证持久化提交的排队上限、幂等响应以及最后一个名额的并发归属。"""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from month04_deploy.app.api import create_app
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_idempotency_budget_integration import (
    read_snapshot,
    store as store,
)
from month04_deploy.tests.test_task_lease_takeover_race_integration import (
    wait_for_both_workers,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def set_queue_limit(store, value):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        row = conn.execute(
            "UPDATE task_runtime_limits SET max_queued_tasks = %s "
            "WHERE singleton_id = 1 RETURNING max_queued_tasks",
            (value,),
        ).fetchone()
        assert row == (value,)


def submit(client, key, text):
    return client.post(
        "/v1/tasks",
        headers={} if key is None else {"Idempotency-Key": key},
        json={"task_type": "demo.echo", "payload": {"text": text}},
    )


def test_queue_limit_defaults_to_32_and_rejects_zero(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        assert conn.execute(
            "SELECT max_running_tasks, max_queued_tasks FROM task_runtime_limits "
            "WHERE singleton_id = 1"
        ).fetchone() == (2, 32)
    with pytest.raises(psycopg.errors.CheckViolation):
        set_queue_limit(store, 0)


def test_full_queue_replays_conflicts_and_rejects_new_work(store):
    set_queue_limit(store, 2)
    with TestClient(create_app(task_store=store)) as client:
        first = submit(client, "M", "original")
        other = submit(client, None, "waiting")
        assert (first.status_code, other.status_code) == (202, 202)
        before = read_snapshot(store)
        assert len(before["tasks"]) == 2

        replay = submit(client, "M", "original")
        assert replay.status_code == 202
        assert replay.json() == first.json()
        assert replay.headers["Location"] == first.headers["Location"]

        conflict = submit(client, "M", "different")
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "idempotency_conflict"
        for key in ("N", None):
            full = submit(client, key, "next")
            assert full.status_code == 503
            assert full.json()["detail"] == "task_queue_full"
            assert "Location" not in full.headers
        assert read_snapshot(store) == before

        # 从 queued 领取到 running 腾出一个排队名额。刚才被拒绝的 N 现在可以提交。
        claimed = store.try_start_task_with_attempt(UUID(other.json()["task_id"]))
        assert claimed is not None and claimed[0]["status"] == "running"
        accepted = submit(client, "N", "next")
        assert accepted.status_code == 202
        previous_ids = {first.json()["task_id"], other.json()["task_id"]}
        assert accepted.json()["task_id"] not in previous_ids
    rows = read_snapshot(store)["tasks"]
    assert sorted(row["status"] for row in rows) == ["queued", "queued", "running"]


def test_full_queue_replays_completed_result(store):
    set_queue_limit(store, 1)
    with TestClient(create_app(task_store=store)) as client:
        first = submit(client, "finished", "original")
        assert first.status_code == 202
        task_id = UUID(first.json()["task_id"])
        claimed = store.try_start_task_with_attempt(task_id)
        assert claimed is not None
        task, attempt = claimed
        result = {"answer": "saved"}
        finished = store.try_succeed_task_with_attempt(
            task_id, attempt["attempt_id"], result,
            expected_lease_version=task["lease_version"],
        )
        assert finished is not None and finished["status"] == "succeeded"
        assert submit(client, "occupant", "waiting").status_code == 202
        before = read_snapshot(store)

        replay = submit(client, "finished", "original")
        assert replay.status_code == 200
        assert replay.json()["task_id"] == str(task_id)
        assert replay.json()["result"] == result
        assert read_snapshot(store) == before


@pytest.mark.parametrize(
    ("second_key", "second_text", "codes"),
    [
        ("M", "first", [202, 202]),
        ("M", "changed", [202, 409]),
        ("N", "first", [202, 503]),
        (None, "first", [202, 503]),
    ],
    ids=["replay", "conflict", "different-keys", "without-key"],
)
def test_two_submissions_compete_for_last_queue_slot(store, second_key, second_text, codes):
    set_queue_limit(store, 2)
    seed = store.create_task("demo.echo", {"text": "already waiting"})
    tag = f"queue_{uuid4().hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    worker_dsn = make_conninfo(
        store.dsn,
        application_name=tag,
        options=f"{options} -c lock_timeout=20s -c statement_timeout=30s",
    )

    def send(key, text):
        with TestClient(create_app(task_store=TaskStore(worker_dsn))) as client:
            return submit(client, key, text)

    requests = [("M", "first"), (second_key, second_text)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        with psycopg.connect(store.dsn, connect_timeout=5) as holder:
            assert holder.execute(
                "SELECT singleton_id FROM task_runtime_limits "
                "WHERE singleton_id = 1 FOR UPDATE"
            ).fetchone() == (1,)
            futures = [pool.submit(send, *request) for request in requests]
            wait_for_both_workers(store.dsn, tag, holder.info.backend_pid, futures)
        responses = [future.result(timeout=30) for future in futures]

    assert sorted(response.status_code for response in responses) == codes
    state = read_snapshot(store)
    assert len(state["tasks"]) == 2 and seed in state["tasks"]
    assert state["attempts"] == [] and state["steps"] == []
    new = next(row for row in state["tasks"] if row["task_id"] != seed["task_id"])
    assert new["status"] == "queued"
    for (key, text), response in zip(requests, responses):
        if response.status_code == 202:
            assert response.json()["task_id"] == str(new["task_id"])
            assert (new["idempotency_key"], new["payload"]) == (key, {"text": text})
        else:
            detail = "idempotency_conflict" if response.status_code == 409 else "task_queue_full"
            assert response.json()["detail"] == detail
