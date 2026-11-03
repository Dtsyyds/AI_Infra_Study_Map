"""用 PostgreSQL 验证过期租约接管和 Attempt 交接。"""

import os
from datetime import timedelta

import psycopg
import pytest

from month04_deploy.app.durable import task_store as task_store_module
from month04_deploy.tests.test_task_attempts_integration import (
    read_attempts,
    read_task,
    store as store,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def first_claim(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        deadline = conn.execute(
            "SELECT clock_timestamp() + INTERVAL '1 hour'"
        ).fetchone()[0]

    created = store.create_task(
        "demo.echo", {"text": "takeover"}, max_attempts=3, retry_deadline_at=deadline
    )
    claim = store.try_start_task_with_attempt(created["task_id"])
    assert claim is not None
    task, attempt = claim
    assert (task["status"], task["lease_version"]) == ("running", 1)
    assert (attempt["status"], attempt["attempt_no"]) == ("running", 1)
    return task, attempt


def set_lease_remaining(store, task, remaining):
    """在独立事务中调整指定版本的期限，测试不必等待真实租约到期。"""
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET lease_expires_at = clock_timestamp() + %s::interval
            WHERE task_id = %s AND lease_version = %s
            RETURNING lease_expires_at
            """,
            (remaining, task["task_id"], task["lease_version"]),
        ).fetchone()
        assert row is not None


def test_takeover_commits_new_lease_and_replaces_running_attempt(store, first_claim):
    first_task, first_attempt = first_claim
    task_id = first_task["task_id"]
    set_lease_remaining(store, first_task, timedelta(seconds=-1))
    with psycopg.connect(store.dsn, autocommit=True, connect_timeout=5) as clock:
        before = clock.execute("SELECT clock_timestamp()").fetchone()[0]
        claim = store.try_take_over_expired_task(task_id, expected_lease_version=1)
        after = clock.execute("SELECT clock_timestamp()").fetchone()[0]

    assert claim is not None
    task, new = claim
    assert (task["task_id"], task["status"], task["lease_version"]) == (
        task_id, "running", 2
    )
    ttl = timedelta(seconds=10)
    assert before + ttl <= task["lease_expires_at"] <= after + ttl
    assert (new["task_id"], new["attempt_no"], new["status"]) == (task_id, 2, "running")
    assert new["attempt_id"] != first_attempt["attempt_id"]
    assert before <= new["started_at"] <= after
    assert new["finished_at"] is None

    assert read_task(store, task_id) == task
    assert read_attempts(store, task_id) == [
        {**first_attempt, "status": "interrupted", "finished_at": new["started_at"]},
        new,
    ]


def test_takeover_insert_failure_rolls_back_and_allows_another_caller(
    store, first_claim, monkeypatch
):
    first_task, first_attempt = first_claim
    task_id = first_task["task_id"]
    set_lease_remaining(store, first_task, timedelta(seconds=-1))
    before_task = read_task(store, task_id)
    assert before_task["lease_version"] == 1
    assert read_attempts(store, task_id) == [first_attempt]

    with monkeypatch.context() as fault:
        fault.setattr(task_store_module, "uuid4", lambda: first_attempt["attempt_id"])
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            store.try_take_over_expired_task(task_id, expected_lease_version=1)
    assert caught.value.diag.constraint_name == "task_attempts_pkey"
    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == [first_attempt]

    # B 回滚之后，C 仍然可以带版本 1 接管。
    claim = store.try_take_over_expired_task(task_id, expected_lease_version=1)
    assert claim is not None
    task, new = claim
    assert (task["lease_version"], new["attempt_no"]) == (2, 2)
    assert new["attempt_id"] != first_attempt["attempt_id"]
    assert read_task(store, task_id) == task
    assert read_attempts(store, task_id) == [
        {**first_attempt, "status": "interrupted", "finished_at": new["started_at"]},
        new,
    ]


def test_takeover_rejects_stale_version_even_when_second_lease_has_expired(
    store, first_claim
):
    first_task, _ = first_claim
    task_id = first_task["task_id"]
    set_lease_remaining(store, first_task, timedelta(seconds=-1))
    second_claim = store.try_take_over_expired_task(task_id, expected_lease_version=1)
    assert second_claim is not None
    second_task, _ = second_claim
    assert second_task["lease_version"] == 2

    set_lease_remaining(store, second_task, timedelta(seconds=-1))
    before_task = read_task(store, task_id)
    before_attempts = read_attempts(store, task_id)
    assert store.try_take_over_expired_task(task_id, expected_lease_version=1) is None
    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == before_attempts

    # 对照组只改变调用方传入的版本，两次调用的数据库前置状态相同。
    third_claim = store.try_take_over_expired_task(task_id, expected_lease_version=2)
    assert third_claim is not None
    third_task, third_attempt = third_claim
    assert (third_task["lease_version"], third_attempt["attempt_no"]) == (3, 3)
    assert read_task(store, task_id) == third_task
    attempts = read_attempts(store, task_id)
    assert [a["status"] for a in attempts] == ["interrupted", "interrupted", "running"]
    assert attempts[-1] == third_attempt


def test_takeover_rejects_matching_version_while_lease_is_live(store, first_claim):
    first_task, first_attempt = first_claim
    task_id = first_task["task_id"]
    set_lease_remaining(store, first_task, timedelta(hours=1))
    before_task = read_task(store, task_id)
    assert store.try_take_over_expired_task(task_id, expected_lease_version=1) is None
    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == [first_attempt]
