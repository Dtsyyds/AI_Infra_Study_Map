import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from month04_deploy.app.durable.idempotency import (
    IdempotencyConflictError,
)
from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def dsn():
    value = os.getenv("AGENT_DATABASE_URL")
    assert value, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    # 只给测试连接设置 SQL 超时，防止异常锁等待长期卡住。
    return make_conninfo(
        value,
        options="-c statement_timeout=10000",
    )


def count_tasks_for_key(dsn, key):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE idempotency_key = %s
                """,
                (key,),
            )
            return cur.fetchone()[0]


def submit_concurrently(dsn, key, payloads):
    barrier = Barrier(len(payloads), timeout=10)

    def submit(payload):
        store = TaskStore(dsn)
        barrier.wait()

        try:
            row = store.create_task_idempotent(
                "demo.echo",
                payload,
                key,
            )
        except IdempotencyConflictError:
            return "conflict", None

        return "ok", row

    with ThreadPoolExecutor(
        max_workers=len(payloads)
    ) as pool:
        futures = [
            pool.submit(submit, payload)
            for payload in payloads
        ]
        return [
            future.result(timeout=30)
            for future in futures
        ]


def test_same_key_same_payload_concurrently(dsn):
    key = f"test-same-{uuid4().hex}"
    payload = {"text": "concurrent same"}

    outcomes = submit_concurrently(
        dsn,
        key,
        [dict(payload) for _ in range(8)],
    )

    assert all(kind == "ok" for kind, _ in outcomes)

    rows = [row for _, row in outcomes]
    assert len({row["task_id"] for row in rows}) == 1
    assert all(row["payload"] == payload for row in rows)
    assert count_tasks_for_key(dsn, key) == 1


def test_same_key_different_payload_concurrently(dsn):
    key = f"test-conflict-{uuid4().hex}"
    payloads = [
        {"text": "request A"},
        {"text": "request B"},
    ]

    outcomes = submit_concurrently(dsn, key, payloads)

    assert sorted(
        kind for kind, _ in outcomes
    ) == ["conflict", "ok"]

    # 结果列表保持输入顺序，所以能找到获胜请求的原始参数。
    winner_index = next(
        index
        for index, (kind, _) in enumerate(outcomes)
        if kind == "ok"
    )
    winner = outcomes[winner_index][1]

    saved = TaskStore(dsn).get_task(winner["task_id"])

    assert saved is not None
    assert saved["payload"] == payloads[winner_index]
    assert count_tasks_for_key(dsn, key) == 1


def test_replay_preserves_succeeded_task(dsn):
    store = TaskStore(dsn)
    key = f"test-finished-{uuid4().hex}"
    payload = {"text": "already finished"}

    created = store.create_task_idempotent(
        "demo.echo", payload, key
    )
    task_id = created["task_id"]

    assert store.try_start_task(task_id) is not None

    expected_result = {"answer": "echo:already finished"}
    finished = store.try_succeed_task(
        task_id,
        expected_result,
    )
    assert finished is not None

    replayed = store.create_task_idempotent(
        "demo.echo", payload, key
    )

    assert replayed["task_id"] == task_id
    assert replayed["status"] == "succeeded"
    assert replayed["result"] == expected_result
    assert replayed["updated_at"] == finished["updated_at"]
    assert count_tasks_for_key(dsn, key) == 1