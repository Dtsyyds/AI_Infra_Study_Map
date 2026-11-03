"""在独立 PostgreSQL schema 中验证 009 到 010 的升级。"""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.tests.test_task_creation_integration import (
    database_before_deadline as database_before_deadline,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def insert_task(conn):
    return conn.execute(
        """
        INSERT INTO tasks (task_id, task_type, payload)
        VALUES (%s, 'demo.echo', %s)
        RETURNING *
        """,
        (uuid4(), Jsonb({"text": "lease migration"})),
    ).fetchone()


@pytest.fixture
def upgraded_task(database_before_deadline):
    # 复用仓库的临时 schema。夹具已运行 001～004，测试结束后会清理。
    dsn, migration_005 = database_before_deadline
    migrations = migration_005.parent
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        for path in (
            migration_005,
            migrations / "006_add_task_max_attempts.sql",
            migrations / "007_add_task_next_retry_at.sql",
            migrations / "008_create_tool_calls.sql",
            migrations / "009_add_tool_call_status.sql",
        ):
            conn.execute(path.read_text(encoding="utf-8"), prepare=False)
        old = insert_task(conn)
        assert old is not None
        assert "lease_version" not in old and "lease_expires_at" not in old

    # 旧 Task 已经提交。用新的连接执行并提交 010。
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        migration = migrations / "010_add_task_lease.sql"
        conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
    return dsn, old


def test_010_preserves_existing_task_and_sets_defaults(upgraded_task):
    dsn, old = upgraded_task
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        after = conn.execute(
            "SELECT * FROM tasks WHERE task_id = %s", (old["task_id"],)
        ).fetchone()
        assert after == {**old, "lease_version": 0, "lease_expires_at": None}

        types = conn.execute(
            """
            SELECT pg_typeof(lease_version)::text AS version_type,
                   pg_typeof(lease_expires_at)::text AS expiry_type
            FROM tasks WHERE task_id = %s
            """,
            (old["task_id"],),
        ).fetchone()
        assert types == {
            "version_type": "bigint",
            "expiry_type": "timestamp with time zone",
        }

        new_task = insert_task(conn)
        assert (new_task["lease_version"], new_task["lease_expires_at"]) == (0, None)
        positive = conn.execute(
            "UPDATE tasks SET lease_version = 1 WHERE task_id = %s RETURNING lease_version",
            (new_task["task_id"],),
        ).fetchone()
        assert positive == {"lease_version": 1}


@pytest.mark.parametrize(
    ("value", "expected_error"),
    [(None, psycopg.errors.NotNullViolation), (-1, psycopg.errors.CheckViolation)],
    ids=["explicit-null", "negative"],
)
def test_010_rejects_invalid_lease_version(upgraded_task, value, expected_error):
    dsn, old = upgraded_task
    with pytest.raises(expected_error):
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute(
                "UPDATE tasks SET lease_version = %s WHERE task_id = %s",
                (value, old["task_id"]),
            )

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT lease_version, lease_expires_at FROM tasks WHERE task_id = %s",
            (old["task_id"],),
        ).fetchone()
        assert row == (0, None)
