"""验证取消请求的登记、重放，以及与首次领取的竞争。"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_retry_integration import deadline_for_creation, wait_for_task_row_block
from month04_deploy.tests.test_worker_attempts_integration import read_snapshot, store as store

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "011_add_task_cancel_request.sql"


def apply_cancel_migration(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)


@pytest.fixture
def cancel_store(store):
    apply_cancel_migration(store)
    return store


def test_migration_does_not_request_cancellation_for_old_or_new_tasks(store):
    old = store.create_task("demo.echo", {"text": "old"})
    apply_cancel_migration(store)
    assert store.get_task(old["task_id"]) == {**old, "cancel_requested_at": None}
    assert store.create_task("demo.echo", {"text": "new"})["cancel_requested_at"] is None


def test_queued_cancel_commits_without_creating_an_attempt(cancel_store):
    store = cancel_store
    task = store.create_task("demo.echo", {"text": "queued"})
    task_id = task["task_id"]
    row = store.request_task_cancel(task_id)
    assert row is not None and row["cancel_requested_at"] is not None
    assert (row["status"], row["lease_version"], row["lease_expires_at"]) == ("cancelled", 0, None)
    assert read_snapshot(store, task_id) == (row, [], [])
    assert store.try_start_task_with_attempt(task_id) is None
    assert store.request_task_cancel(task_id) == row
    assert read_snapshot(store, task_id) == (row, [], [])


def test_running_request_preserves_attempt_checkpoint_and_first_time(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "running"})["task_id"]
    running, attempt = store.try_start_task_with_attempt(task_id)
    step = store.try_save_completed_step(
        task_id, 0, "read_input_v1", {"text": "running"},
        expected_lease_version=running["lease_version"], expected_attempt_id=attempt["attempt_id"],
    )
    assert step is not None
    row = store.request_task_cancel(task_id)
    assert row is not None and row["cancel_requested_at"] is not None
    assert row == {**running, "cancel_requested_at": row["cancel_requested_at"]}
    assert store.request_task_cancel(task_id) == row
    assert read_snapshot(store, task_id) == (row, [attempt], [step])


def test_replay_reads_the_request_time_from_an_already_confirmed_task(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "replay"})["task_id"]
    requested = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
    confirmed = requested.replace(second=1)
    # 准备已确认的记录，仅验证取消接口的回读。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "UPDATE tasks SET status = 'cancelled', cancel_requested_at = %s, updated_at = %s "
            "WHERE task_id = %s", (requested, confirmed, task_id),
        )
    before = store.get_task(task_id)
    row = store.request_task_cancel(task_id)
    assert row == before
    assert (row["status"], row["cancel_requested_at"], row["updated_at"]) == (
        "cancelled", requested, confirmed,
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_completed_task_without_retry_is_returned_unchanged(cancel_store, status):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "terminal"})["task_id"]
    running, attempt = store.try_start_task_with_attempt(task_id)
    finish = store.try_succeed_task_with_attempt if status == "succeeded" else store.try_fail_task_with_attempt
    terminal = finish(
        task_id, attempt["attempt_id"], {"message": "original"},
        expected_lease_version=running["lease_version"],
    )
    assert terminal is not None and terminal["status"] == status
    before = read_snapshot(store, task_id)
    assert terminal["cancel_requested_at"] is None
    assert store.request_task_cancel(task_id) == terminal
    assert read_snapshot(store, task_id) == before


def test_cancelling_a_scheduled_retry_clears_the_plan_and_preserves_its_attempt(cancel_store):
    store = cancel_store
    task = store.create_task(
        "demo.echo", {"text": "scheduled"}, retry_deadline_at=deadline_for_creation(store),
        max_attempts=2,
    )
    task_id = task["task_id"]
    running, attempt = store.try_start_task_with_attempt(task_id)
    attempt_id = attempt["attempt_id"]
    failed = store.try_fail_task_with_attempt(
        task_id, attempt_id, {"message": "transient"}, expected_lease_version=running["lease_version"],
    )
    assert failed is not None
    policy = dict(retryable_error=True, outcome_unknown=False, replay_safe=True)
    scheduled = store.try_schedule_task_retry(task_id, attempt_id, delay_seconds=0, **policy)
    assert scheduled["next_retry_at"] is not None
    _, attempts, steps = read_snapshot(store, task_id)
    row = store.request_task_cancel(task_id)
    assert row is not None and row["cancel_requested_at"] is not None
    assert (row["status"], row["next_retry_at"]) == ("cancelled", None)
    assert store.try_retry_task_with_attempt(task_id, attempt_id, **policy) is None
    assert read_snapshot(store, task_id) == (row, attempts, steps)


def test_missing_task_returns_none(cancel_store):
    assert cancel_store.request_task_cancel(uuid4()) is None


@pytest.mark.parametrize("first,second", [("cancel", "claim"), ("claim", "cancel"), ("cancel", "cancel")])
def test_concurrent_cancel_and_claim_follow_the_committed_order(cancel_store, monkeypatch, first, second):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "race"})["task_id"]
    if first == second:
        # 两个客户端同时取消同一个 running 任务。
        assert store.try_start_task_with_attempt(task_id) is not None

    tag = f"cancel_race_{task_id.hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    contender = TaskStore(make_conninfo(
        store.dsn, application_name=tag,
        options=f"{options} -c lock_timeout=15s -c statement_timeout=20s",
    ))
    first_call = store.request_task_cancel if first == "cancel" else store.try_start_task_with_attempt
    second_call = contender.request_task_cancel if second == "cancel" else contender.try_start_task_with_attempt
    with ThreadPoolExecutor(max_workers=1) as pool:
        with (
            psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as holder,
            psycopg.connect(store.dsn, row_factory=dict_row, autocommit=True, connect_timeout=5) as monitor,
        ):
            # 第一项执行真实方法，暂由测试控制其事务提交，以便观察第二个连接的行锁等待。
            with monkeypatch.context() as patch:
                patch.setattr(store, "_connect", lambda: nullcontext(holder))
                first_result = first_call(task_id)
            assert first_result is not None
            waiting = pool.submit(second_call, task_id)
            wait_for_task_row_block(monitor, tag, holder.info.backend_pid, waiting)
        second_result = waiting.result(timeout=15)

    task, attempts, steps = read_snapshot(store, task_id)
    assert task["cancel_requested_at"] is not None and steps == []
    if second == "claim":
        assert second_result is None
        assert task == first_result and task["status"] == "cancelled" and attempts == []
    else:
        assert task == second_result and task["status"] == "running"
        assert [attempt["status"] for attempt in attempts] == ["running"]
        if first == "cancel":
            assert first_result == second_result
        else:
            claimed_task, attempt = first_result
            assert claimed_task["cancel_requested_at"] is None
            assert attempts == [attempt]
