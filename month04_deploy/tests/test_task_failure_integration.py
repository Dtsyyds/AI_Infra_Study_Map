"""真实 PostgreSQL：Task 与对应 Attempt 的失败事务验收（8 个用例）。

放置位置：month04_deploy/tests/test_task_failure_integration.py
前提：TaskStore 已实现 try_fail_task_with_attempt；保留已验收的领取和成功方法。

在仓库根目录运行（同一终端已设置 AGENT_DATABASE_URL）：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -q month04_deploy/tests/test_task_failure_integration.py

每个用例创建随机 schema，仅应用 001/004 迁移，结束后删除自己的 schema。
账号需有 CREATE SCHEMA 权限；故障注入只影响临时 schema。
不 mock SQL 或连接；从新连接检查提交或回滚后的完整记录。
本文件只验收存储方法，尚不验收 Worker 对业务异常的处理。
"""

import os
from pathlib import Path
from uuid import UUID, uuid4

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

    schema_name = f"task_fail_test_{uuid4().hex}"
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
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def read_state(store: TaskStore, task_id: UUID):
    """独立连接读回 Task 和它的所有 Attempts，包含时间戳与错误字段。"""
    with psycopg.connect(
        store.dsn, row_factory=dict_row, connect_timeout=5
    ) as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE task_id = %s", (task_id,)
        ).fetchone()
        attempts = conn.execute(
            """
            SELECT * FROM task_attempts
            WHERE task_id = %s
            ORDER BY attempt_no
            """,
            (task_id,),
        ).fetchall()
        return task, attempts


def start_running(store: TaskStore):
    created = store.create_task("demo.echo", {"text": "failure acceptance"})
    claimed = store.try_start_task_with_attempt(created["task_id"])
    assert claimed is not None
    task, attempt = claimed
    assert task["status"] == "running"
    assert attempt["status"] == "running"
    return task, attempt


def test_failure_commits_error_to_task_and_existing_attempt(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    error = {
        "type": "ValueError",
        "message": "payload.text 必须是长度为 1～10000 的字符串",
        "context": {"step_name": "read_input_v1", "retryable": False},
    }

    # 人工放入旧值，确保新方法真的清空 result、覆盖旧 error。
    # 这是本测试的准备数据，不是正常 Worker 会产生的执行结果。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "UPDATE tasks SET result = %s, error = %s WHERE task_id = %s",
            (Jsonb({"stale": True}), Jsonb({"message": "old"}), task_id),
        )
    before_task, before_attempts = read_state(store, task_id)
    assert before_task is not None
    assert before_attempts == [attempt]

    failed = store.try_fail_task_with_attempt(
        task_id, attempt["attempt_id"], error
    )

    assert failed is not None
    assert failed["updated_at"] >= before_task["updated_at"]
    assert failed == {
        **before_task,
        "status": "failed",
        "result": None,
        "error": error,
        "updated_at": failed["updated_at"],
    }
    saved_task, saved_attempts = read_state(store, task_id)
    assert saved_task == failed
    assert len(saved_attempts) == 1
    finished_attempt = saved_attempts[0]
    assert finished_attempt["finished_at"] is not None
    assert finished_attempt["finished_at"] >= attempt["started_at"]
    # ID、编号、开始时间等原值都保留；失败收尾不追加新 Attempt。
    assert finished_attempt == {
        **attempt,
        "status": "failed",
        "error": error,
        "finished_at": finished_attempt["finished_at"],
    }


@pytest.mark.parametrize("bad_attempt_kind", ["other_task", "missing"])
def test_wrong_attempt_returns_none_without_changes(store, bad_attempt_kind):
    target_task, _ = start_running(store)
    other_task, other_attempt = start_running(store)
    task_id = target_task["task_id"]
    other_task_id = other_task["task_id"]
    before_target = read_state(store, task_id)
    before_other = read_state(store, other_task_id)
    wrong_attempt_id = (
        other_attempt["attempt_id"] if bad_attempt_kind == "other_task" else uuid4()
    )

    assert store.try_fail_task_with_attempt(
        task_id, wrong_attempt_id, {"message": "must not be saved"}
    ) is None

    assert read_state(store, task_id) == before_target
    assert read_state(store, other_task_id) == before_other


def test_repeated_failure_preserves_first_error_and_finish_time(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    first_error = {"type": "ValueError", "message": "first failure"}
    failed = store.try_fail_task_with_attempt(
        task_id, attempt["attempt_id"], first_error
    )
    assert failed is not None
    before_task, before_attempts = read_state(store, task_id)
    assert before_task["status"] == "failed"
    assert before_task["error"] == first_error
    assert len(before_attempts) == 1
    assert before_attempts[0]["status"] == "failed"
    assert before_attempts[0]["error"] == first_error
    assert before_attempts[0]["finished_at"] is not None

    assert store.try_fail_task_with_attempt(
        task_id, attempt["attempt_id"], {"message": "must not replace first error"}
    ) is None

    assert read_state(store, task_id) == (before_task, before_attempts)


def test_succeeded_task_cannot_be_overwritten_as_failed(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    result = {"answer": "echo:already finished"}
    succeeded = store.try_succeed_task_with_attempt(
        task_id, attempt["attempt_id"], result
    )
    assert succeeded is not None
    assert succeeded["status"] == "succeeded"
    assert succeeded["result"] == result
    before = read_state(store, task_id)

    assert store.try_fail_task_with_attempt(
        task_id, attempt["attempt_id"], {"message": "late failure"}
    ) is None

    assert read_state(store, task_id) == before


def test_closed_attempt_is_rejected_before_task_is_modified(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    # 人工构造：Task 仍 running，但传入的 Attempt 已结束。
    # 方法应正常拒绝，不能先改 Task 再因 Attempt 不匹配而抛错。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            UPDATE task_attempts
            SET status = 'interrupted',
                error = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE attempt_id = %s
            """,
            (Jsonb({"message": "previously interrupted"}), attempt["attempt_id"]),
        )
    before = read_state(store, task_id)
    assert before[0]["status"] == "running"
    assert before[1][0]["status"] == "interrupted"

    assert store.try_fail_task_with_attempt(
        task_id, attempt["attempt_id"], {"message": "must not overwrite history"}
    ) is None

    assert read_state(store, task_id) == before


def test_attempt_sql_error_rolls_back_task_failure(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    before = read_state(store, task_id)

    # 真实 CHECK 错误，只阻止本用例中的 Attempt 转为 failed。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            ALTER TABLE task_attempts
            ADD CONSTRAINT test_attempt_failure_rejected
            CHECK (status <> 'failed')
        """)

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        store.try_fail_task_with_attempt(
            task_id, attempt["attempt_id"], {"message": "must roll back"}
        )

    assert caught.value.diag.table_name == "task_attempts"
    assert caught.value.diag.constraint_name == "test_attempt_failure_rejected"
    # 回滚到已提交的 running/running，连 error 和时间戳也恢复原值。
    assert read_state(store, task_id) == before


def test_attempt_zero_rows_raises_and_rolls_back_task_failure(store):
    task, attempt = start_running(store)
    task_id = task["task_id"]
    before = read_state(store, task_id)

    # BEFORE 行触发器返回 NULL 会跳过更新，SQL 本身不报错。
    # UPDATE ... RETURNING 因而返回 0 行，让真实 fetchone() 返回 None。
    # 触发器仅是本用例的故障注入工具，不应加入生产迁移。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_skip_attempt_failure()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $test_trigger$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM tasks
                    WHERE task_id = OLD.task_id
                      AND status = 'failed'
                      AND error = NEW.error
                ) THEN
                    RAISE EXCEPTION 'test expected Task failure update first';
                END IF;
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_skip_attempt_failure
            BEFORE UPDATE ON task_attempts
            FOR EACH ROW
            WHEN (NEW.status = 'failed')
            EXECUTE FUNCTION test_skip_attempt_failure()
        """)

    # 必须抛 RuntimeError 触发回滚；正常返回 None 会提交前面的 Task 更新。
    with pytest.raises(RuntimeError):
        store.try_fail_task_with_attempt(
            task_id, attempt["attempt_id"], {"message": "must also roll back"}
        )

    assert read_state(store, task_id) == before
