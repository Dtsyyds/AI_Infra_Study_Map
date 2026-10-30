"""真实 PostgreSQL：首次领取、成功完成 Task 与 Attempt 的事务验收。

放置位置：month04_deploy/tests/test_task_attempts_integration.py
前提：已将 try_start_task_with_attempt 和 try_succeed_task_with_attempt
      加入现有 TaskStore，
      004_create_task_attempts.sql 已保存到 month04_deploy/migrations/。

在仓库根目录运行（同一终端已设置 AGENT_DATABASE_URL）：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -q month04_deploy/tests/test_task_attempts_integration.py

每个测试创建独立 schema，仅应用 001/004 迁移，结束后删除该 schema。
共 9 个测试用例。使用真实连接、事务与数据库约束，不 mock SQL 或连接。
故障注入：重复 UUID、临时 CHECK 约束、返回 NULL 的 BEFORE UPDATE 触发器。
约束和触发器仅创建在当前测试的随机 schema，随该 schema 一起删除。
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
        migrations_dir / "005_add_task_retry_deadline.sql",
        migrations_dir / "006_add_task_max_attempts.sql",
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


def test_success_commits_task_result_and_attempt_finish(store):
    created = store.create_task("demo.echo", {"text": "finish task"})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    running_task, running_attempt = claimed
    result = {"text": "执行成功", "steps": [0, 1, 2], "meta": {"retried": False}}

    completed = store.try_succeed_task_with_attempt(
        task_id, running_attempt["attempt_id"], result
    )

    assert completed is not None
    assert completed["task_id"] == task_id
    assert completed["status"] == "succeeded"
    assert completed["result"] == result
    assert completed["error"] is None
    assert completed["updated_at"] >= running_task["updated_at"]
    assert read_task(store, task_id) == completed

    attempts = read_attempts(store, task_id)
    assert len(attempts) == 1
    finished = attempts[0]
    assert finished["finished_at"] is not None
    assert finished["finished_at"] >= finished["started_at"]
    assert finished == {
        **running_attempt,
        "status": "succeeded",
        "error": None,
        "finished_at": finished["finished_at"],
    }


@pytest.mark.parametrize("bad_attempt_kind", ["other_task", "missing"])
def test_wrong_attempt_does_not_modify_either_task(store, bad_attempt_kind):
    target = store.create_task("demo.echo", {"text": "target"})
    other = store.create_task("demo.echo", {"text": "other task"})
    target_claim = store.try_start_task_with_attempt(target["task_id"])
    other_claim = store.try_start_task_with_attempt(other["task_id"])
    assert target_claim is not None
    assert other_claim is not None
    target_task, target_attempt = target_claim
    other_task, other_attempt = other_claim

    if bad_attempt_kind == "other_task":
        wrong_attempt_id = other_attempt["attempt_id"]
    else:
        wrong_attempt_id = uuid4()

    completed = store.try_succeed_task_with_attempt(
        target["task_id"], wrong_attempt_id, {"text": "must not be saved"}
    )

    assert completed is None
    assert read_task(store, target["task_id"]) == target_task
    assert read_attempts(store, target["task_id"]) == [target_attempt]
    assert read_task(store, other["task_id"]) == other_task
    assert read_attempts(store, other["task_id"]) == [other_attempt]


def test_repeated_success_preserves_first_result_and_finish_time(store):
    created = store.create_task("demo.echo", {"text": "repeat finish"})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    _, attempt = claimed
    attempt_id = attempt["attempt_id"]
    first_result = {"text": "first result"}

    completed = store.try_succeed_task_with_attempt(task_id, attempt_id, first_result)
    assert completed is not None
    before_task = read_task(store, task_id)
    before_attempts = read_attempts(store, task_id)
    assert before_task is not None
    assert before_task["status"] == "succeeded"
    assert before_task["result"] == first_result
    assert len(before_attempts) == 1
    assert before_attempts[0]["status"] == "succeeded"

    assert store.try_succeed_task_with_attempt(
        task_id, attempt_id, {"text": "must not overwrite the first result"}
    ) is None

    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == before_attempts


def test_attempt_update_sql_error_rolls_back_task_success(store):
    created = store.create_task("demo.echo", {"text": "SQL error rollback"})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    before_task, before_attempt = claimed

    # 仅在本测试 schema 中阻止 Attempt 变为 succeeded；running 记录仍合法。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            ALTER TABLE task_attempts
            ADD CONSTRAINT test_attempt_finish_rejected
            CHECK (status <> 'succeeded')
        """)

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        store.try_succeed_task_with_attempt(
            task_id, before_attempt["attempt_id"], {"text": "must roll back"}
        )

    assert caught.value.diag.table_name == "task_attempts"
    assert caught.value.diag.constraint_name == "test_attempt_finish_rejected"
    # 包括 status/result/error/updated_at/finished_at，所有字段均应恢复原值。
    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == [before_attempt]


def test_attempt_update_zero_rows_raises_and_rolls_back_task_success(store):
    created = store.create_task("demo.echo", {"text": "zero rows rollback"})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    before_task, before_attempt = claimed

    # 触发器只是验收工具，不属于生产迁移或 TaskStore 实现。
    # PostgreSQL 的 BEFORE 行触发器返回 NULL 时跳过这次行更新，SQL 不报错。
    # 因而 UPDATE ... RETURNING 返回 0 行，真实 cur.fetchone() 得到 None。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_skip_attempt_finish()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $test_trigger$
            BEGIN
                -- 确认先前的 Task 更新已在同一事务内发生。
                IF NOT EXISTS (
                    SELECT 1 FROM tasks
                    WHERE task_id = OLD.task_id AND status = 'succeeded'
                ) THEN
                    RAISE EXCEPTION 'test expected Task update before Attempt update';
                END IF;
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_skip_attempt_finish
            BEFORE UPDATE ON task_attempts
            FOR EACH ROW
            WHEN (NEW.status = 'succeeded')
            EXECUTE FUNCTION test_skip_attempt_finish()
        """)

    # 此处必须是业务 RuntimeError，不能是 SQL 错误，也不能正常 return None。
    with pytest.raises(RuntimeError):
        store.try_succeed_task_with_attempt(
            task_id, before_attempt["attempt_id"], {"text": "must also roll back"}
        )

    assert read_task(store, task_id) == before_task
    assert read_attempts(store, task_id) == [before_attempt]
