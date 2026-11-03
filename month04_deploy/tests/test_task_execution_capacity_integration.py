import os

import psycopg
import pytest

from month04_deploy.tests.test_worker_attempts_integration import store as store


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1", reason="需要 PostgreSQL 集成测试"
)


@pytest.fixture
def capacity_store(store):
    return store


def test_running_limit_can_be_changed(capacity_store):
    query = "SELECT singleton_id, max_running_tasks FROM task_runtime_limits"
    with psycopg.connect(capacity_store.dsn, connect_timeout=5) as conn:
        assert conn.execute(query).fetchall() == [(1, 2)]
        conn.execute(
            "UPDATE task_runtime_limits SET max_running_tasks = 1 WHERE singleton_id = 1"
        )

    # 从新连接读取，确认修改已经提交。
    with psycopg.connect(capacity_store.dsn, connect_timeout=5) as conn:
        assert conn.execute(query).fetchall() == [(1, 1)]


@pytest.mark.parametrize(("statement", "error"), [
    (
        "INSERT INTO task_runtime_limits (singleton_id, max_running_tasks) VALUES (1, 2)",
        psycopg.errors.UniqueViolation,
    ),
    (
        "INSERT INTO task_runtime_limits (singleton_id, max_running_tasks) VALUES (2, 1)",
        psycopg.errors.CheckViolation,
    ),
    (
        "UPDATE task_runtime_limits SET max_running_tasks = 0 WHERE singleton_id = 1",
        psycopg.errors.CheckViolation,
    ),
])
def test_invalid_running_limit_is_rejected(capacity_store, statement, error):
    with pytest.raises(error):
        with psycopg.connect(capacity_store.dsn, connect_timeout=5) as conn:
            conn.execute(statement)