"""真实 PostgreSQL：首次领取任务与创建 Attempt 的事务验收。

放置位置：month04_deploy/tests/test_task_attempts_integration.py
前提：已将 try_start_task_with_attempt 加入现有 TaskStore，
      004_create_task_attempts.sql 已保存到 month04_deploy/migrations/。

在仓库根目录运行（同一终端已设置 AGENT_DATABASE_URL）：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -q month04_deploy/tests/test_task_attempts_integration.py

每个测试创建独立 schema，仅应用 001/004 迁移，结束后删除该 schema。
使用真实连接、事务与数据库约束；故障测试只替换 UUID 生成结果。
测试账号需要拥有目标数据库的 CREATE SCHEMA 权限。
"""

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable import task_store as task_store_module
from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = [
        migrations_dir / "001_create_tasks.sql",
        migrations_dir / "004_create_task_attempts.sql",
    ]
    for migration in migrations:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    # 名称只包含固定前缀和 UUID 十六进制字符；不复用业务 schema。
    schema_name = f"attempt_test_{uuid4().hex}"
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
            for migration in migrations:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

        yield TaskStore(scoped_dsn)
    finally:
        # 只删除本测试成功创建的随机 schema，以及其中的测试数据。
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def read_task(store: TaskStore, task_id: UUID):
    """新连接读取：验证调用方返回后，数据已经真正提交。"""
    with psycopg.connect(
        store.dsn, row_factory=dict_row, connect_timeout=5
    ) as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE task_id = %s",
            (task_id,),
        ).fetchone()


def read_attempts(store: TaskStore, task_id: UUID):
    with psycopg.connect(
        store.dsn, row_factory=dict_row, connect_timeout=5
    ) as conn:
        return conn.execute(
            """
            SELECT * FROM task_attempts
            WHERE task_id = %s
            ORDER BY attempt_no
            """,
            (task_id,),
        ).fetchall()


def test_first_start_commits_task_and_attempt(store):
    created = store.create_task("demo.echo", {"text": "first attempt"})
    task_id = created["task_id"]

    claimed = store.try_start_task_with_attempt(task_id)

    assert claimed is not None
    task, attempt = claimed
    assert task["task_id"] == task_id
    assert task["status"] == "running"
    assert attempt["task_id"] == task_id
    assert isinstance(attempt["attempt_id"], UUID)
    assert attempt["attempt_no"] == 1
    assert attempt["status"] == "running"
    assert attempt["started_at"] is not None
    assert attempt["finished_at"] is None
    assert attempt["error"] is None

    # 不仅检查 UPDATE/INSERT RETURNING，还从新连接读回提交结果。
    assert read_task(store, task_id) == task
    assert read_attempts(store, task_id) == [attempt]


def test_repeated_start_does_not_change_task_or_append_attempt(store):
    created = store.create_task("demo.echo", {"text": "repeat claim"})
    task_id = created["task_id"]
    assert store.try_start_task_with_attempt(task_id) is not None

    before_task = read_task(store, task_id)
    before_attempts = read_attempts(store, task_id)
    assert len(before_attempts) == 1

    assert store.try_start_task_with_attempt(task_id) is None

    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == before_attempts


def test_attempt_insert_failure_rolls_back_task_update(store, monkeypatch):
    # 先合法创建一个已有 Attempt，后面故意复用它的主键。
    owner = store.create_task("demo.echo", {"text": "existing attempt"})
    owner_claim = store.try_start_task_with_attempt(owner["task_id"])
    assert owner_claim is not None
    owner_task, existing_attempt = owner_claim

    # 必须在替换 uuid4 之前创建待测任务，避免干扰 create_task。
    target = store.create_task("demo.echo", {"text": "rollback target"})
    task_id = target["task_id"]
    before_task = read_task(store, task_id)
    assert before_task is not None
    assert before_task["status"] == "queued"
    assert read_attempts(store, task_id) == []

    monkeypatch.setattr(
        task_store_module,
        "uuid4",
        lambda: existing_attempt["attempt_id"],
    )

    # execute/连接/事务均没有被 mock：UniqueViolation 来自真实数据库。
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        store.try_start_task_with_attempt(task_id)

    assert caught.value.diag.table_name == "task_attempts"
    assert caught.value.diag.constraint_name == "task_attempts_pkey"

    after_task = read_task(store, task_id)
    assert after_task is not None
    assert after_task["status"] == "queued"
    assert after_task["updated_at"] == before_task["updated_at"]
    assert after_task == before_task
    assert read_attempts(store, task_id) == []

    # 回滚不能删除或改写此前已经提交的其他任务及 Attempt。
    assert read_task(store, owner["task_id"]) == owner_task
    assert read_attempts(store, owner["task_id"]) == [existing_attempt]
