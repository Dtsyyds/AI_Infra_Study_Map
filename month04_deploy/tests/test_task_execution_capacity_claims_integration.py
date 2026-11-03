"""在独立 PostgreSQL 连接上验证首次领取、重试和接管怎样占用执行名额。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from month04_deploy.app.durable.retry_policy import RetryDecision
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_task_lease_takeover_race_integration import wait_for_both_workers
from month04_deploy.tests.test_task_retry_integration import (
    deadline_for_creation,
    make_failed_task,
    retry,
    schedule_retry_now,
)
from month04_deploy.tests.test_worker_attempts_integration import store as store


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def one_slot_store(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        row = conn.execute(
            """
            UPDATE task_runtime_limits
            SET max_running_tasks = 1
            WHERE singleton_id = 1
            RETURNING max_running_tasks
            """
        ).fetchone()
        assert row == (1,)
    return store


def test_two_tasks_compete_for_last_slot(one_slot_store):
    store = one_slot_store
    deadline = deadline_for_creation(store)
    tasks = [
        store.create_task(
            "demo.echo", {"text": name}, max_attempts=2, retry_deadline_at=deadline
        )
        for name in ("first", "second")
    ]
    tag = f"capacity_{tasks[0]['task_id'].hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    worker_dsn = make_conninfo(
        store.dsn,
        application_name=tag,
        options=f"{options} -c lock_timeout=20s -c statement_timeout=30s",
    )

    def claim_task(task_id):
        return TaskStore(worker_dsn).try_start_task_with_attempt(task_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with psycopg.connect(store.dsn, connect_timeout=5) as holder:
            row = holder.execute(
                "SELECT singleton_id FROM task_runtime_limits "
                "WHERE singleton_id = 1 FOR UPDATE"
            ).fetchone()
            assert row == (1,)
            futures = [pool.submit(claim_task, task["task_id"]) for task in tasks]
            wait_for_both_workers(store.dsn, tag, holder.info.backend_pid, futures)
        # 释放配置行锁后才等待领取结果。胜者保持 running，直到本测试主动完成它。
        claims = [future.result(timeout=30) for future in futures]

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    active, first_attempt = winners[0]
    queued = next(task for task, claim in zip(tasks, claims) if claim is None)
    queued_id = queued["task_id"]
    assert active["status"] == "running"
    assert store.get_task(active["task_id"]) == active
    assert store.get_task_attempts(active["task_id"]) == [first_attempt]
    assert store.get_task(queued_id) == queued
    assert store.get_task_attempts(queued_id) == []

    set_lease_remaining(store, active, timedelta(seconds=-1))
    assert store.try_start_task_with_attempt(queued_id) is None
    taken = store.try_take_over_expired_task(
        active["task_id"], expected_lease_version=active["lease_version"]
    )
    assert taken is not None
    successor, attempt = taken
    assert successor["lease_version"] == active["lease_version"] + 1
    assert attempt["attempt_no"] == first_attempt["attempt_no"] + 1

    finished = store.try_succeed_task_with_attempt(
        successor["task_id"],
        attempt["attempt_id"],
        {"answer": "ok"},
        expected_lease_version=successor["lease_version"],
    )
    assert finished is not None and finished["status"] == "succeeded"
    assert store.try_start_task_with_attempt(queued_id) is not None


def test_full_capacity_preserves_due_retry(one_slot_store):
    store = one_slot_store
    task_id, attempt_id = make_failed_task(store)
    plan = schedule_retry_now(store, task_id, attempt_id)
    attempts_before = store.get_task_attempts(task_id)

    owner = store.create_task("demo.echo", {"text": "occupies the slot"})
    waiting = store.create_task("demo.echo", {"text": "waiting"})
    assert store.try_start_task(owner["task_id"]) is not None
    assert store.try_start_task(waiting["task_id"]) is None

    decision = retry(store, task_id, attempt_id, retryable_error=False)
    assert decision is RetryDecision.STOP
    decision = retry(store, task_id, attempt_id, outcome_unknown=True, replay_safe=False)
    assert decision is RetryDecision.RECONCILE
    assert retry(store, task_id, attempt_id) is None
    assert store.get_task(task_id) == plan
    assert store.get_task_attempts(task_id) == attempts_before

    assert store.try_succeed_task(owner["task_id"], {"answer": "done"}) is not None
    claimed = retry(store, task_id, attempt_id)
    assert isinstance(claimed, tuple)
    started_task, new_attempt = claimed
    assert started_task["status"] == "running"
    assert started_task["next_retry_at"] is None
    assert new_attempt["attempt_no"] == 2
