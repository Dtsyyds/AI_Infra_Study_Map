"""在独立 PostgreSQL schema 中验证超时写回、清理确认和执行名额。"""

import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.tests.test_task_cancel_request_integration import cancel_store as cancel_store
from month04_deploy.tests.test_task_creation_integration import (
    database_before_deadline as database_before_deadline,
)
from month04_deploy.tests.test_task_execution_capacity_claims_integration import (
    one_slot_store as one_slot_store,
)
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_task_retry_integration import deadline_for_creation, retry, schedule_retry_now
from month04_deploy.tests.test_worker_attempts_integration import store as store


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def start_task(store):
    task = store.create_task(
        "demo.echo", {"text": "A"}, max_attempts=3, retry_deadline_at=deadline_for_creation(store),
    )
    claim = store.try_start_task_with_attempt(task["task_id"])
    assert claim is not None
    return claim


def time_out(store, running, attempt):
    return store.try_fail_task_with_attempt(
        running["task_id"], attempt["attempt_id"], {"type": "ToolTimeout"},
        expected_lease_version=running["lease_version"], cleanup_pending=True,
    )


def test_old_current_and_duplicate_confirmations(one_slot_store):
    store = one_slot_store
    first, attempt1 = start_task(store)
    a = first["task_id"]
    id1 = attempt1["attempt_id"]
    assert time_out(store, first, attempt1)["cleanup_attempt_id"] == id1
    assert store.try_confirm_task_cleanup(a, id1) == a

    schedule_retry_now(store, a, id1)
    second, attempt2 = retry(store, a, id1)
    id2 = attempt2["attempt_id"]
    assert attempt2["attempt_no"] == 2 and id2 != id1
    pending = time_out(store, second, attempt2)
    assert (pending["status"], pending["cleanup_pending"], pending["cleanup_attempt_id"]) == (
        "failed", True, id2,
    )
    history = store.get_task_attempts(a)
    assert [row["status"] for row in history] == ["failed", "failed"]
    queued = store.create_task("demo.echo", {"text": "B"})
    b = queued["task_id"]

    # 首次确认已经提交，但 #1 的确认消息被再投递一次。
    assert store.try_confirm_task_cleanup(a, id1) is None
    assert store.get_task(a) == pending
    assert store.try_start_task_with_attempt(b) is None
    assert store.get_task(b) == queued
    assert store.get_task_attempts(b) == []

    assert store.try_confirm_task_cleanup(a, id2) == a
    released = {**pending, "cleanup_pending": False, "cleanup_attempt_id": None}
    assert store.get_task(a) == released
    b_task, b_attempt = store.try_start_task_with_attempt(b)

    assert store.try_confirm_task_cleanup(a, id2) is None
    assert store.get_task(a) == released
    assert store.get_task_attempts(a) == history
    assert store.get_task(b) == b_task
    assert store.get_task_attempts(b) == [b_attempt]


def test_pending_task_cannot_retry_even_with_a_free_slot(store):
    first, attempt = start_task(store)
    a, id1 = first["task_id"], attempt["attempt_id"]
    time_out(store, first, attempt)
    plan = schedule_retry_now(store, a, id1)
    before = store.get_task_attempts(a)
    assert retry(store, a, id1) is None
    assert store.get_task(a) == plan and store.get_task_attempts(a) == before

    # 默认上限为 2。B 可用第二个名额，C 无法领取。
    b = store.create_task("demo.echo", {"text": "B"})["task_id"]
    c = store.create_task("demo.echo", {"text": "C"})["task_id"]
    assert store.try_start_task(b) is not None
    assert store.try_start_task(c) is None
    assert store.try_confirm_task_cleanup(a, id1) == a
    second, new_attempt = retry(store, a, id1)
    assert second["next_retry_at"] is None and new_attempt["attempt_no"] == 2


def test_cancel_wins_but_keeps_cleanup_ownership(cancel_store):
    store = cancel_store
    task, attempt = start_task(store)
    a, id1 = task["task_id"], attempt["attempt_id"]
    store.request_task_cancel(a)
    ended = time_out(store, task, attempt)
    assert (ended["status"], ended["cleanup_pending"], ended["cleanup_attempt_id"]) == (
        "cancelled", True, id1,
    )
    assert store.get_task_attempts(a)[0]["status"] == "interrupted"
    assert store.try_confirm_task_cleanup(a, id1) == a
    assert store.get_task(a)["status"] == "cancelled"


def test_stale_lease_and_failed_registration_leave_task_and_attempt_running(store):
    task, attempt = start_task(store)
    a, id1 = task["task_id"], attempt["attempt_id"]
    assert store.try_fail_task_with_attempt(
        a, id1, {"type": "ToolTimeout"}, expected_lease_version=task["lease_version"] - 1,
        cleanup_pending=True,
    ) is None
    assert store.get_task(a) == task and store.get_task_attempts(a) == [attempt]

    # 注入最后一步写入故障，观察先前的 Task / Attempt 终态写入是否一起回滚。
    with psycopg.connect(store.dsn) as conn:
        conn.execute("ALTER TABLE tasks ADD CONSTRAINT test_reject_cleanup CHECK (NOT cleanup_pending)")
    with pytest.raises(psycopg.errors.CheckViolation):
        time_out(store, task, attempt)
    assert store.get_task(a) == task and store.get_task_attempts(a) == [attempt]


def test_running_and_pending_count_once_and_cannot_be_taken_over(store):
    task, attempt = start_task(store)
    a = task["task_id"]
    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE tasks SET cleanup_pending = TRUE, cleanup_attempt_id = %s WHERE task_id = %s",
            (attempt["attempt_id"], a),
        )
    set_lease_remaining(store, task, timedelta(seconds=-1))
    assert store.try_take_over_expired_task(a, expected_lease_version=task["lease_version"]) is None
    b = store.create_task("demo.echo", {"text": "B"})["task_id"]
    c = store.create_task("demo.echo", {"text": "C"})["task_id"]
    assert store.try_start_task_with_attempt(b) is not None
    assert store.try_start_task_with_attempt(c) is None


def test_schema_rejects_missing_owner_and_owner_from_another_task(store):
    task, attempt = start_task(store)
    b = store.create_task("demo.echo", {"text": "B"})["task_id"]
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(store.dsn) as conn:
        conn.execute("UPDATE tasks SET cleanup_pending = TRUE WHERE task_id = %s", (task["task_id"],))
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE tasks SET cleanup_attempt_id = %s WHERE task_id = %s",
            (attempt["attempt_id"], task["task_id"]),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE tasks SET cleanup_pending = TRUE, cleanup_attempt_id = %s WHERE task_id = %s",
            (attempt["attempt_id"], b),
        )


def test_migration_preserves_preexisting_tasks(database_before_deadline):
    dsn, migration_005 = database_before_deadline
    migrations = migration_005.parent
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for path in sorted(migrations.glob("[0-9][0-9][0-9]_*.sql")):
            if 5 <= int(path.name[:3]) <= 13:
                conn.execute(path.read_text(encoding="utf-8"), prepare=False)
        old = conn.execute(
            "INSERT INTO tasks (task_id, task_type, payload) VALUES (%s, 'demo.echo', %s) RETURNING *",
            (uuid4(), Jsonb({"text": "existing"})),
        ).fetchone()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute((migrations / "014_add_task_cleanup.sql").read_text(encoding="utf-8"), prepare=False)
        row = conn.execute("SELECT * FROM tasks WHERE task_id = %s", (old["task_id"],)).fetchone()
    assert row == {**old, "cleanup_pending": False, "cleanup_attempt_id": None}
