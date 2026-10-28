"""真实 PostgreSQL：任务创建时持久化重试截止时间（8 个用例）。

放置位置：month04_deploy/tests/test_task_deadline_creation_integration.py
接口：TaskStore.create_task(..., retry_deadline_at: datetime | None = None)。

在项目根目录、agent-dev 环境且已设置 AGENT_DATABASE_URL 后运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short -x -q \
        month04_deploy/tests/test_task_deadline_creation_integration.py

每个用例创建独立随机 schema，执行真实迁移，最后删除自己的 schema。
验证提交后的数据时使用新连接，不 mock SQL、连接或 fetchone。
这个文件不会给 AGENT_DATABASE_URL 原先指向的 tasks 表补迁移。
本轮仅验证迁移和创建入口，不声称重试事务已读取 Task 中的截止时间。
"""

import os
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def database_before_deadline():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    previous_migrations = [
        migrations_dir / "001_create_tasks.sql",
        migrations_dir / "002_create_task_steps.sql",
        migrations_dir / "003_add_task_idempotency.sql",
        migrations_dir / "004_create_task_attempts.sql",
    ]
    deadline_migration = migrations_dir / "005_add_task_retry_deadline.sql"
    for migration in [*previous_migrations, deadline_migration]:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    schema_name = f"deadline_create_test_{uuid4().hex}"
    admin_dsn = make_conninfo(
        base_dsn,
        options="-c statement_timeout=10000 -c lock_timeout=5000",
    )
    scoped_dsn = make_conninfo(
        base_dsn,
        options=(
            f"-c search_path={schema_name} "
            "-c statement_timeout=10000 -c lock_timeout=5000"
        ),
    )

    with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
        )

    try:
        with psycopg.connect(scoped_dsn, connect_timeout=5) as conn:
            assert conn.execute("SELECT current_schema()").fetchone()[0] == schema_name
            for migration in previous_migrations:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
        yield scoped_dsn, deadline_migration
    finally:
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )


@pytest.fixture
def store(database_before_deadline):
    dsn, deadline_migration = database_before_deadline
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(deadline_migration.read_text(encoding="utf-8"), prepare=False)
    return TaskStore(dsn)


def read_tasks(dsn):
    """每次通过新连接回读，证明数据已提交。"""
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        return conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()


def test_migration_preserves_existing_task_and_adds_nullable_column_without_default(
    database_before_deadline,
):
    dsn, deadline_migration = database_before_deadline
    task_id = uuid4()

    # 模拟 005 之前已经存在的任务：这里只使用旧表已有的字段。
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, task_type, payload) VALUES (%s, %s, %s)",
            (task_id, "demo.echo", Jsonb({"text": "created before migration 005"})),
        )
    before = read_tasks(dsn)
    assert len(before) == 1
    assert "retry_deadline_at" not in before[0]

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(deadline_migration.read_text(encoding="utf-8"), prepare=False)

    after = read_tasks(dsn)
    assert after == [{**before[0], "retry_deadline_at": None}]

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        column = conn.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'tasks'
              AND column_name = 'retry_deadline_at'
            """
        ).fetchone()
    assert column == ("timestamp with time zone", "YES", None)


@pytest.mark.parametrize("explicit_none", [False, True], ids=["omitted", "explicit-none"])
def test_create_without_deadline_commits_null(store, explicit_none):
    kwargs = {"retry_deadline_at": None} if explicit_none else {}
    payload = {"text": "no retry budget configured"}

    created = store.create_task("demo.echo", payload, **kwargs)

    assert created["status"] == "queued"
    assert created["payload"] == payload
    assert created["retry_deadline_at"] is None
    assert read_tasks(store.dsn) == [created]


@pytest.mark.parametrize(
    "offset_hours", [0, 8, -7], ids=["utc", "utc-plus-eight", "utc-minus-seven"]
)
def test_create_with_aware_deadline_commits_same_instant(store, offset_hours):
    deadline = datetime(
        2030, 1, 2, 3, 4, 5, 123456,
        tzinfo=timezone(timedelta(hours=offset_hours)),
    )

    created = store.create_task(
        "demo.echo", {"text": "fixed deadline"}, retry_deadline_at=deadline
    )

    # 比较同一个时间点，不要求 PostgreSQL 保留输入的时区显示形式。
    assert created["retry_deadline_at"] == deadline
    assert created["retry_deadline_at"].utcoffset() is not None
    saved = read_tasks(store.dsn)
    assert saved == [created]
    assert saved[0]["retry_deadline_at"] == deadline.astimezone(timezone.utc)


class NoOffsetTimezone(tzinfo):
    """tzinfo 存在，但无法提供 UTC 偏移，仍不是有效的 aware datetime。"""

    def utcoffset(self, dt):
        return None


class ConnectionCountingStore(TaskStore):
    """只统计连接调用，若被调用仍返回原来的真实连接。"""

    def __init__(self, dsn):
        super().__init__(dsn)
        self.connect_calls = 0

    def _connect(self):
        self.connect_calls += 1
        return super()._connect()


@pytest.mark.parametrize(
    "deadline",
    [
        datetime(2030, 1, 2, 3, 4, 5),
        datetime(2030, 1, 2, 3, 4, 5, tzinfo=NoOffsetTimezone()),
    ],
    ids=["no-tzinfo", "no-utc-offset"],
)
def test_invalid_timezone_is_rejected_before_connecting_and_inserting(store, deadline):
    before = read_tasks(store.dsn)
    checked_store = ConnectionCountingStore(store.dsn)

    with pytest.raises(ValueError, match="时区"):
        checked_store.create_task(
            "demo.echo", {"text": "invalid deadline"}, retry_deadline_at=deadline
        )

    assert checked_store.connect_calls == 0
    assert read_tasks(store.dsn) == before
