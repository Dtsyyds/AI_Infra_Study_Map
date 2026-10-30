"""真实 PostgreSQL：任务创建时持久化重试截止时间和次数上限（23 个用例）。

放置位置：month04_deploy/tests/test_task_creation_integration.py
接口：TaskStore.create_task(..., retry_deadline_at=None, max_attempts=1)。

在项目根目录、agent-dev 环境且已设置 AGENT_DATABASE_URL 后运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short -x -q \
        month04_deploy/tests/test_task_creation_integration.py

每个用例创建独立随机 schema，执行真实迁移，最后删除自己的 schema。
验证提交后的数据时使用新连接，不 mock SQL、连接或 fetchone。
这个文件不会给 AGENT_DATABASE_URL 原先指向的 tasks 表补迁移。
本文件验证 005、006 迁移及创建入口；重试读取持久化上限由重试测试另行验证。
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
def database_before_max_attempts(database_before_deadline):
    """在独立 schema 中应用 005，保留尚未执行 006 的状态。"""
    dsn, deadline_migration = database_before_deadline
    max_attempts_migration = deadline_migration.with_name("006_add_task_max_attempts.sql")
    assert max_attempts_migration.is_file(), f"缺少迁移文件：{max_attempts_migration}"
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(deadline_migration.read_text(encoding="utf-8"), prepare=False)
    return dsn, max_attempts_migration


@pytest.fixture
def store(database_before_max_attempts):
    dsn, max_attempts_migration = database_before_max_attempts
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(max_attempts_migration.read_text(encoding="utf-8"), prepare=False)
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


def test_max_attempts_migration_preserves_existing_task_and_sets_default_one(
    database_before_max_attempts,
):
    dsn, max_attempts_migration = database_before_max_attempts
    task_id = uuid4()
    deadline = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    # 用旧 schema 支持的 SQL 创建任务，不调用已经依赖 006 的 create_task。
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO tasks (task_id, task_type, payload, retry_deadline_at)
            VALUES (%s, %s, %s, %s)
            """,
            (
                task_id,
                "demo.echo",
                Jsonb({"text": "created before migration 006"}),
                deadline,
            ),
        )
    before = read_tasks(dsn)
    assert len(before) == 1
    assert "max_attempts" not in before[0]

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(max_attempts_migration.read_text(encoding="utf-8"), prepare=False)

    assert read_tasks(dsn) == [{**before[0], "max_attempts": 1}]
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        column = conn.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'tasks'
              AND column_name = 'max_attempts'
            """
        ).fetchone()
        constraint = conn.execute(
            """
            SELECT contype, convalidated
            FROM pg_constraint
            WHERE conrelid = 'tasks'::regclass
              AND conname = 'tasks_max_attempts_positive'
            """
        ).fetchone()
    assert column == ("integer", "NO", "1")
    assert constraint == ("c", True)


@pytest.mark.parametrize(
    "kwargs, expected_max_attempts",
    [({}, 1), ({"max_attempts": 1}, 1), ({"max_attempts": 3}, 3)],
    ids=["omitted", "explicit-one", "explicit-three"],
)
def test_create_commits_max_attempts_and_deadline_together(
    store, kwargs, expected_max_attempts,
):
    deadline = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    payload = {"text": "fixed retry budget"}

    created = store.create_task(
        "demo.echo", payload, retry_deadline_at=deadline, **kwargs
    )

    assert created["status"] == "queued"
    assert created["payload"] == payload
    assert created["max_attempts"] == expected_max_attempts
    assert created["retry_deadline_at"] == deadline
    assert read_tasks(store.dsn) == [created]


@pytest.mark.parametrize(
    "max_attempts",
    [None, True, False, "3", 3.0],
    ids=["none", "true", "false", "string", "float"],
)
def test_invalid_max_attempts_type_is_rejected_before_connecting(store, max_attempts):
    before = read_tasks(store.dsn)
    checked_store = ConnectionCountingStore(store.dsn)

    with pytest.raises(TypeError, match="max_attempts"):
        checked_store.create_task(
            "demo.echo", {"text": "invalid type"}, max_attempts=max_attempts
        )

    assert checked_store.connect_calls == 0
    assert read_tasks(store.dsn) == before


@pytest.mark.parametrize("max_attempts", [0, -1], ids=["zero", "negative"])
def test_nonpositive_max_attempts_is_rejected_before_connecting(store, max_attempts):
    before = read_tasks(store.dsn)
    checked_store = ConnectionCountingStore(store.dsn)

    with pytest.raises(ValueError, match="max_attempts"):
        checked_store.create_task(
            "demo.echo", {"text": "invalid value"}, max_attempts=max_attempts
        )

    assert checked_store.connect_calls == 0
    assert read_tasks(store.dsn) == before


def test_database_default_applies_when_insert_omits_max_attempts(store):
    # 直接省略列，验证数据库 DEFAULT；create_task 会显式传入 Python 默认值。
    with psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as conn:
        created = conn.execute(
            """
            INSERT INTO tasks (task_id, task_type, payload)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (uuid4(), "demo.echo", Jsonb({"text": "database default"})),
        ).fetchone()

    assert created["max_attempts"] == 1
    assert read_tasks(store.dsn) == [created]


@pytest.mark.parametrize(
    "max_attempts, error_type",
    [
        (None, psycopg.errors.NotNullViolation),
        (0, psycopg.errors.CheckViolation),
        (-1, psycopg.errors.CheckViolation),
    ],
    ids=["explicit-null", "zero", "negative"],
)
def test_database_rejects_invalid_max_attempts_without_inserting(
    store, max_attempts, error_type,
):
    before = read_tasks(store.dsn)

    # 绕过 Python 校验，验证数据库约束及失败插入的回滚。
    with pytest.raises(error_type) as caught:
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute(
                """
                INSERT INTO tasks (task_id, task_type, payload, max_attempts)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    "demo.echo",
                    Jsonb({"text": "database constraint"}),
                    max_attempts,
                ),
            )

    if max_attempts is None:
        assert caught.value.diag.column_name == "max_attempts"
    else:
        assert caught.value.diag.constraint_name == "tasks_max_attempts_positive"
    assert read_tasks(store.dsn) == before
