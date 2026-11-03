"""两个独立 Worker 同时接管同一份已经过期的租约。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from time import monotonic, sleep

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_attempts_integration import (
    read_attempts,
    read_task,
    store as store,
)
from month04_deploy.tests.test_task_lease_takeover_integration import (
    first_claim as first_claim,
    set_lease_remaining,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def wait_for_both_workers(dsn, worker_tag, holder_pid, futures):
    """两个 Worker 都排进持锁连接的等待队列后才放行。"""
    deadline = monotonic() + 10
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as observer:
        while True:
            waiting = observer.execute(
                """
                SELECT pid, pg_blocking_pids(pid)
                FROM pg_stat_activity
                WHERE datname = current_database() AND application_name = %s
                  AND wait_event_type = 'Lock'
                """,
                (worker_tag,),
            ).fetchall()

            # 第二个 Worker 可能等在第一个 Worker 后面。
            direct = {pid for pid, blockers in waiting if holder_pid in blockers}
            if len(waiting) == 2 and all(
                pid in direct or direct.intersection(blockers) for pid, blockers in waiting
            ):
                return

            for future in futures:
                if future.done():
                    future.result()  # 若 Worker 抛出异常，保留原始错误。
                    pytest.fail("测试连接仍持有 Task 行锁，Worker 已提前返回")
            if monotonic() >= deadline:
                pytest.fail(f"未观察到两个 Worker 争抢 Task 行锁，等待关系为 {waiting}")
            sleep(0.02)


def test_two_workers_compete_for_one_expired_lease(store, first_claim):
    first_task, first_attempt = first_claim
    task_id = first_task["task_id"]
    set_lease_remaining(store, first_task, timedelta(seconds=-1))

    # 保留夹具的 search_path，给测试观察行锁留出时间。
    tag = f"w5_takeover_{task_id.hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    worker_dsn = make_conninfo(
        store.dsn,
        application_name=tag,
        options=f"{options} -c lock_timeout=20s -c statement_timeout=30s",
    )
    ready = Barrier(2, timeout=10)

    def claim():
        worker = TaskStore(worker_dsn)
        ready.wait()
        return worker.try_take_over_expired_task(task_id, expected_lease_version=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with psycopg.connect(store.dsn, connect_timeout=5) as holder:
            row = holder.execute(
                "SELECT task_id FROM tasks WHERE task_id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            assert row == (task_id,)
            futures = [pool.submit(claim) for _ in range(2)]
            wait_for_both_workers(store.dsn, tag, holder.info.backend_pid, futures)
        # 测试连接释放行锁后再等待两个 Worker 的返回值。
        results = [future.result(timeout=30) for future in futures]

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    task, new_attempt = winners[0]
    assert (task["task_id"], task["status"], task["lease_version"]) == (task_id, "running", 2)
    assert new_attempt["task_id"] == task_id
    assert (new_attempt["attempt_no"], new_attempt["status"]) == (2, "running")
    assert new_attempt["attempt_id"] != first_attempt["attempt_id"]
    assert new_attempt["finished_at"] is None
    assert read_task(store, task_id) == task
    assert read_attempts(store, task_id) == [
        {**first_attempt, "status": "interrupted", "finished_at": new_attempt["started_at"]},
        new_attempt,
    ]
