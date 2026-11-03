"""用真实 PostgreSQL 验证 TaskStore 的一次心跳续租。"""

import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

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
def claimed(store):
    created = store.create_task("demo.echo", {"text": "heartbeat"})
    claim = store.try_start_task_with_attempt(created["task_id"])
    assert claim is not None
    return claim


def assert_renewal_refused(store, task_id, expected_version):
    before = read_task(store, task_id)
    attempts = read_attempts(store, task_id)
    assert store.try_renew_task_lease(task_id, expected_version) is None
    assert read_task(store, task_id) == before
    assert read_attempts(store, task_id) == attempts


def test_heartbeat_commits_new_expiry_with_same_version_and_attempt(store, claimed):
    task, attempt = claimed
    task_id = task["task_id"]
    bound_sql = "SELECT clock_timestamp() + INTERVAL '10 seconds'"
    with psycopg.connect(store.dsn, autocommit=True, connect_timeout=5) as clock:
        earliest = clock.execute(bound_sql).fetchone()[0]
        renewed = store.try_renew_task_lease(task_id, task["lease_version"])
        latest = clock.execute(bound_sql).fetchone()[0]

    assert renewed is not None
    expiry = renewed["lease_expires_at"]
    assert expiry is not None
    assert earliest <= expiry <= latest
    assert expiry > task["lease_expires_at"]
    assert renewed == {**task, "lease_expires_at": expiry}
    assert read_task(store, task_id) == renewed
    assert read_attempts(store, task_id) == [attempt]


def test_heartbeat_rejects_old_version_while_new_lease_is_live(store, claimed):
    task, _ = claimed
    # 构造另一个 Worker 已获授新版本的状态。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        version, = conn.execute(
            """
            UPDATE tasks SET lease_version = lease_version + 1,
                             lease_expires_at = clock_timestamp() + INTERVAL '1 hour'
            WHERE task_id = %s RETURNING lease_version
            """,
            (task["task_id"],),
        ).fetchone()
    assert version == task["lease_version"] + 1
    assert_renewal_refused(store, task["task_id"], task["lease_version"])


@pytest.mark.parametrize(
    ("version", "remaining"),
    [(1, timedelta(seconds=-1)), (0, None)],
    ids=["expired", "legacy-null"],
)
def test_heartbeat_requires_an_unexpired_lease(store, claimed, version, remaining):
    task, _ = claimed
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            UPDATE tasks SET lease_version = %s,
                             lease_expires_at = clock_timestamp() + %s::interval
            WHERE task_id = %s
            """,
            (version, remaining, task["task_id"]),
        )
    # 版本仍匹配，此处单独验证到期时间的限制。
    assert_renewal_refused(store, task["task_id"], version)


def test_heartbeat_rejects_a_completed_task(store, claimed):
    task, attempt = claimed
    completed = store.try_succeed_task_with_attempt(
        task["task_id"], attempt["attempt_id"], {"answer": "done"},
        expected_lease_version=task["lease_version"],
    )
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert_renewal_refused(store, task["task_id"], task["lease_version"])


def test_heartbeat_returns_none_for_missing_task(store):
    assert_renewal_refused(store, uuid4(), 1)
