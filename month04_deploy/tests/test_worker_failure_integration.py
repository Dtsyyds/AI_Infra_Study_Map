"""Worker 失败处理验收：8 个用例，使用真实 PostgreSQL。

放置位置：month04_deploy/tests/test_worker_failure_integration.py
前提：TaskStore 的领取、成功、失败方法已实现，Worker 已接入失败方法。

在仓库根目录、已设置 AGENT_DATABASE_URL 的终端运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -q month04_deploy/tests/test_worker_failure_integration.py

每例在随机 schema 中应用 001/002/004 迁移，结束后删除自己的 schema。
账号需有 CREATE SCHEMA 权限。所有触发器和约束都是临时故障注入工具，
不要加入生产迁移。

7 个用例运行真实独立 Worker 进程，并用新连接检查持久化记录。
最后 1 个直接调用真实 Worker，仅替换 save_completed_step 来注入保存前的
ValueError，验证异常捕获范围；其余存储方法仍连接真实数据库。
人工恢复用例先在测试中准备已领取状态，不存在同时运行的旧 Worker。
"""

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.app.durable.task_store import TaskStore


REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_ERROR_MESSAGE = "payload.text 必须是长度为 1～10000 的字符串"

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    migrations_dir = REPO_ROOT / "month04_deploy" / "migrations"
    migrations = [
        migrations_dir / "001_create_tasks.sql",
        migrations_dir / "002_create_task_steps.sql",
        migrations_dir / "004_create_task_attempts.sql",
        migrations_dir / "005_add_task_retry_deadline.sql",
    ]
    for migration in migrations:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    schema_name = f"worker_fail_test_{uuid4().hex}"
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
        # subprocess.run 返回或超时抛错时，子进程均已结束。
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def run_worker(store: TaskStore, task_id: UUID, *extra_args: str):
    worker_env = os.environ.copy()
    worker_env["AGENT_DATABASE_URL"] = store.dsn
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "month04_deploy.app.durable.worker",
            "--task-id",
            str(task_id),
            *extra_args,
        ],
        cwd=REPO_ROOT,
        env=worker_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def read_snapshot(store: TaskStore, task_id: UUID):
    with psycopg.connect(
        store.dsn, row_factory=dict_row, connect_timeout=5
    ) as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE task_id = %s", (task_id,)
        ).fetchone()
        attempts = conn.execute(
            "SELECT * FROM task_attempts WHERE task_id = %s ORDER BY attempt_no",
            (task_id,),
        ).fetchall()
        steps = conn.execute(
            "SELECT * FROM task_steps WHERE task_id = %s ORDER BY step_index",
            (task_id,),
        ).fetchall()
    return task, attempts, steps


def prepare_claimed_task(store: TaskStore, payload):
    """只准备已提交的领取记录，没有启动旧 Worker。"""
    created = store.create_task("demo.echo", payload)
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    task, attempt = claimed
    assert task["status"] == "running"
    assert attempt["status"] == "running"
    return task_id


def assert_worker_error(process, error_type: str, message: str):
    assert process.returncode == 1, process.stdout + process.stderr
    assert f"{error_type}:" in process.stderr, process.stdout + process.stderr
    assert message in process.stderr, process.stdout + process.stderr
    assert "SUCCEEDED result=" not in process.stdout
    assert "NOT_ELIGIBLE:" not in process.stdout


def assert_input_failure_saved(snapshot, task_id: UUID):
    task, attempts, steps = snapshot
    error = {
        "type": "ValueError",
        "message": INPUT_ERROR_MESSAGE,
        "step_index": 0,
        "step_name": "read_input_v1",
    }
    assert task is not None
    assert task["task_id"] == task_id
    assert task["status"] == "failed"
    assert task["result"] is None
    assert task["error"] == error
    assert task["updated_at"] >= task["created_at"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert isinstance(attempt["attempt_id"], UUID)
    assert attempt["task_id"] == task_id
    assert attempt["attempt_no"] == 1
    assert attempt["status"] == "failed"
    assert attempt["error"] == error
    assert attempt["finished_at"] is not None
    assert attempt["finished_at"] >= attempt["started_at"]
    assert steps == []


def assert_still_running(snapshot):
    task, attempts, _ = snapshot
    assert task is not None
    assert task["status"] == "running"
    assert task["result"] is None
    assert task["error"] is None
    assert len(attempts) == 1
    assert attempts[0]["status"] == "running"
    assert attempts[0]["error"] is None
    assert attempts[0]["finished_at"] is None


def test_invalid_input_commits_failure_then_worker_exits_with_value_error(store):
    created = store.create_task("demo.echo", {"text": 123})
    task_id = created["task_id"]

    process = run_worker(store, task_id)

    assert_worker_error(process, "ValueError", INPUT_ERROR_MESSAGE)
    assert "STEP_SAVED index=" not in process.stdout
    # 进程因为重新抛出的 ValueError 退出后，failed 记录仍然已经提交。
    assert_input_failure_saved(read_snapshot(store, task_id), task_id)
    assert store.get_running_attempt(task_id) is None


@pytest.mark.parametrize("mode", ["normal", "resume"])
def test_failed_task_is_not_reexecuted_or_given_another_attempt(store, mode):
    created = store.create_task("demo.echo", {"text": 123})
    task_id = created["task_id"]
    first = run_worker(store, task_id)
    assert_worker_error(first, "ValueError", INPUT_ERROR_MESSAGE)
    before = read_snapshot(store, task_id)
    assert_input_failure_saved(before, task_id)

    extra_args = ("--resume",) if mode == "resume" else ()
    second = run_worker(store, task_id, *extra_args)

    assert second.returncode == 2, second.stdout + second.stderr
    assert "NOT_ELIGIBLE:" in second.stdout
    assert "SUCCEEDED result=" not in second.stdout
    assert "STEP_SAVED index=" not in second.stdout
    assert read_snapshot(store, task_id) == before


def test_rejected_failure_record_raises_runtime_error_without_changes(store):
    task_id = prepare_claimed_task(store, {"text": 123})
    before = read_snapshot(store, task_id)
    assert_still_running(before)
    assert before[2] == []

    # 跳过 Task 的 failed 更新，让真实 try_fail_task_with_attempt 返回 None。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_reject_task_failure()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $test_trigger$
            BEGIN
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_reject_task_failure
            BEFORE UPDATE ON tasks
            FOR EACH ROW
            WHEN (NEW.status = 'failed')
            EXECUTE FUNCTION test_reject_task_failure()
        """)

    process = run_worker(store, task_id, "--resume")

    assert_worker_error(process, "RuntimeError", "failure recording rejected")
    assert f"ValueError: {INPUT_ERROR_MESSAGE}" in process.stderr
    assert "STEP_SAVED index=" not in process.stdout
    assert read_snapshot(store, task_id) == before


def test_failure_record_sql_error_propagates_and_rolls_back(store):
    task_id = prepare_claimed_task(store, {"text": 123})
    before = read_snapshot(store, task_id)
    assert_still_running(before)
    assert before[2] == []

    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            ALTER TABLE task_attempts
            ADD CONSTRAINT test_reject_attempt_failure
            CHECK (status <> 'failed')
        """)

    process = run_worker(store, task_id, "--resume")

    assert_worker_error(process, "CheckViolation", "test_reject_attempt_failure")
    assert "STEP_SAVED index=" not in process.stdout
    # 业务确实失败了，但记录失败的事务未提交，不能声称已经保存 failed。
    assert read_snapshot(store, task_id) == before


def test_checkpoint_sql_error_keeps_running_and_committed_prefix(store):
    payload_text = "checkpoint database failure"
    created = store.create_task("demo.echo", {"text": payload_text})
    task_id = created["task_id"]
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            ALTER TABLE task_steps
            ADD CONSTRAINT test_reject_second_checkpoint
            CHECK (step_index <> 1)
        """)

    process = run_worker(store, task_id)

    assert_worker_error(process, "CheckViolation", "test_reject_second_checkpoint")
    snapshot = read_snapshot(store, task_id)
    assert_still_running(snapshot)
    steps = snapshot[2]
    assert len(steps) == 1
    assert steps[0]["task_id"] == task_id
    assert steps[0]["step_index"] == 0
    assert steps[0]["step_name"] == "read_input_v1"
    assert steps[0]["result"] == {"text": payload_text}
    assert steps[0]["completed_at"] is not None
    assert "STEP_SAVED index=0 name=read_input_v1" in process.stdout
    assert "STEP_SAVED index=1 " not in process.stdout
    assert "STEP_SAVED index=2 " not in process.stdout


def test_restore_validation_error_does_not_mark_task_as_business_failure(store):
    task_id = prepare_claimed_task(store, {"text": "restore validation"})
    # 构造已保存步骤与当前流程不兼容的情况；不改动生产迁移。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO task_steps (task_id, step_index, step_name, result)
            VALUES (%s, %s, %s, %s)
            """,
            (task_id, 0, "old_workflow_step", Jsonb({"text": "saved input"})),
        )
    before = read_snapshot(store, task_id)
    assert_still_running(before)
    assert len(before[2]) == 1

    process = run_worker(store, task_id, "--resume")

    assert_worker_error(process, "ValueError", "保存的步骤名称与当前流程不匹配")
    assert "STEP_SAVED index=" not in process.stdout
    assert read_snapshot(store, task_id) == before


def test_checkpoint_value_error_is_outside_business_error_handler(store, monkeypatch):
    from month04_deploy.app.durable.worker import run_task_once

    payload_text = "checkpoint serialization failure"
    task_id = prepare_claimed_task(store, {"text": payload_text})
    before = read_snapshot(store, task_id)
    assert_still_running(before)
    assert before[2] == []
    injected = ValueError("injected checkpoint serialization failure")
    save_calls = []

    def fail_before_saving(_self, saved_task_id, step_index, step_name, result):
        save_calls.append((saved_task_id, step_index, step_name, result))
        raise injected

    # 唯一的方法替换：模拟保存前的 ValueError，明确检查 try/except 的边界。
    # 其他 TaskStore 方法不替换，因此错误捕获范围过大会真实写成 failed。
    monkeypatch.setattr(TaskStore, "save_completed_step", fail_before_saving)

    with pytest.raises(ValueError) as caught:
        run_task_once(store, task_id, resume=True)

    assert caught.value is injected
    assert save_calls == [(task_id, 0, "read_input_v1", {"text": payload_text})]
    assert read_snapshot(store, task_id) == before
