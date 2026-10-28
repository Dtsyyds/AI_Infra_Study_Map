"""真实 PostgreSQL + 独立 Worker 进程：Attempt 接入验收（8 个用例）。

放置位置：month04_deploy/tests/test_worker_attempts_integration.py
前提：TaskStore 已包含 try_start_task_with_attempt、get_running_attempt、
      try_succeed_task_with_attempt，Worker 已接入这些方法。

在仓库根目录、已设置 AGENT_DATABASE_URL 的终端运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -q month04_deploy/tests/test_worker_attempts_integration.py

每例创建独立随机 schema，应用仓库中的 001/002/004 迁移；结束时只删除
该 schema。子进程通过 AGENT_DATABASE_URL 使用同一个测试 schema。
账号需要 CREATE SCHEMA 权限。不要把故障注入触发器加进业务迁移。

覆盖正常执行、重复领取拒绝、无检查点恢复、三个真实进程崩溃点、
恢复时缺少 Attempt、完成更新被拒绝。没有 mock SQL、连接或 Worker。
范围：人工确认旧 Worker 退出、没有并发执行；不代表自动接管或重试验收。
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable.task_store import TaskStore


REPO_ROOT = Path(__file__).resolve().parents[2]
STEP_NAMES = ["read_input_v1", "build_echo_v1", "format_result_v1"]

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

    schema_name = f"worker_attempt_test_{uuid4().hex}"
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
        # run_worker 使用 subprocess.run；返回或超时抛错时，子进程均已结束。
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def run_worker(store: TaskStore, task_id: UUID, *extra_args: str):
    worker_env = os.environ.copy()
    # 关键：不能让子进程沿用未限定 schema 的原始连接串。
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
    """子进程退出后，用新连接直接读取真实持久化数据。"""
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


def saved_indices(process):
    return [
        int(index)
        for index in re.findall(
            r"^STEP_SAVED index=(\d+) name=", process.stdout, flags=re.MULTILINE
        )
    ]


def expected_outputs(payload_text: str):
    return [
        {"text": payload_text},
        {"text": f"echo:{payload_text}"},
        {"answer": f"echo:{payload_text}"},
    ]


def assert_finished_snapshot(snapshot, task_id: UUID, payload_text: str):
    task, attempts, steps = snapshot
    assert task is not None
    assert task["task_id"] == task_id
    assert task["status"] == "succeeded"
    assert task["result"] == expected_outputs(payload_text)[-1]
    assert task["error"] is None

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["task_id"] == task_id
    assert isinstance(attempt["attempt_id"], UUID)
    assert attempt["attempt_no"] == 1
    assert attempt["status"] == "succeeded"
    assert attempt["error"] is None
    assert attempt["finished_at"] is not None
    assert attempt["finished_at"] >= attempt["started_at"]

    assert [step["step_index"] for step in steps] == [0, 1, 2]
    assert [step["step_name"] for step in steps] == STEP_NAMES
    assert [step["result"] for step in steps] == expected_outputs(payload_text)
    assert all(step["task_id"] == task_id for step in steps)
    assert all(step["completed_at"] is not None for step in steps)


def assert_same_attempt_finished(before_attempt, after_attempt):
    # attempt_id、attempt_no、started_at 等字段必须原样保留。
    assert after_attempt == {
        **before_attempt,
        "status": "succeeded",
        "error": None,
        "finished_at": after_attempt["finished_at"],
    }


def test_normal_worker_finishes_task_and_one_attempt(store):
    payload_text = "normal worker"
    created = store.create_task("demo.echo", {"text": payload_text})
    task_id = created["task_id"]

    process = run_worker(store, task_id)

    assert process.returncode == 0, process.stdout + process.stderr
    assert "SUCCEEDED result=" in process.stdout
    assert saved_indices(process) == [0, 1, 2]
    assert_finished_snapshot(read_snapshot(store, task_id), task_id, payload_text)
    assert store.get_running_attempt(task_id) is None


def test_second_normal_worker_is_rejected_without_changing_data(store):
    created = store.create_task("demo.echo", {"text": "already claimed"})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    before = read_snapshot(store, task_id)
    assert before[0]["status"] == "running"
    assert len(before[1]) == 1
    assert before[2] == []

    # 没有 --resume：已有 running Task 不能被再次首次领取。
    process = run_worker(store, task_id)

    assert process.returncode == 2, process.stdout + process.stderr
    assert "NOT_ELIGIBLE:" in process.stdout
    assert "SUCCEEDED result=" not in process.stdout
    assert saved_indices(process) == []
    assert read_snapshot(store, task_id) == before


def test_resume_before_first_checkpoint_reuses_claimed_attempt(store):
    payload_text = "claimed without checkpoint"
    created = store.create_task("demo.echo", {"text": payload_text})
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert claimed is not None
    _, before_attempt = claimed
    assert read_snapshot(store, task_id)[2] == []

    # 人工准备“领取已提交、没有步骤记录”的状态；此例不宣称发生了进程崩溃。
    process = run_worker(store, task_id, "--resume")

    assert process.returncode == 0, process.stdout + process.stderr
    assert "RESTORED next_step=0" in process.stdout
    assert "SUCCEEDED result=" in process.stdout
    assert saved_indices(process) == [0, 1, 2]
    after = read_snapshot(store, task_id)
    assert_finished_snapshot(after, task_id, payload_text)
    assert_same_attempt_finished(before_attempt, after[1][0])


@pytest.mark.parametrize("crash_step", [0, 1, 2])
def test_real_crash_resume_keeps_attempt_and_committed_prefix(store, crash_step):
    payload_text = f"attempt crash {crash_step}"
    created = store.create_task("demo.echo", {"text": payload_text})
    task_id = created["task_id"]

    crashed = run_worker(store, task_id, "--crash-after-step", str(crash_step))

    assert crashed.returncode == 86, crashed.stdout + crashed.stderr
    assert f"INJECTED_CRASH after_step={crash_step}" in crashed.stdout
    assert "SUCCEEDED result=" not in crashed.stdout
    assert saved_indices(crashed) == list(range(crash_step + 1))
    before_task, before_attempts, before_steps = read_snapshot(store, task_id)
    assert before_task is not None
    assert before_task["status"] == "running"
    assert before_task["result"] is None
    assert len(before_attempts) == 1
    before_attempt = before_attempts[0]
    assert before_attempt["attempt_no"] == 1
    assert before_attempt["status"] == "running"
    assert before_attempt["finished_at"] is None
    assert store.get_running_attempt(task_id) == before_attempt
    assert [step["step_index"] for step in before_steps] == list(
        range(crash_step + 1)
    )
    assert [step["step_name"] for step in before_steps] == STEP_NAMES[:crash_step + 1]
    assert [step["result"] for step in before_steps] == expected_outputs(
        payload_text
    )[:crash_step + 1]

    # subprocess.run 已等待旧进程退出；不存在与恢复进程重叠执行的窗口。
    resumed = run_worker(store, task_id, "--resume")

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert f"RESTORED next_step={crash_step + 1}" in resumed.stdout
    assert "SUCCEEDED result=" in resumed.stdout
    assert saved_indices(resumed) == list(range(crash_step + 1, 3))
    after = read_snapshot(store, task_id)
    assert_finished_snapshot(after, task_id, payload_text)
    assert_same_attempt_finished(before_attempt, after[1][0])
    # 包括 completed_at 在内，已提交前缀必须保持原样。
    assert after[2][:len(before_steps)] == before_steps
    assert store.get_running_attempt(task_id) is None


def test_resume_missing_running_attempt_raises_without_executing_steps(store):
    created = store.create_task("demo.echo", {"text": "missing attempt"})
    task_id = created["task_id"]
    # 故意在本测试 schema 制造遗留/不一致状态：running Task 没有 Attempt。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            UPDATE tasks SET status = 'running', updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
            """,
            (task_id,),
        )
    before = read_snapshot(store, task_id)
    assert before[0]["status"] == "running"
    assert before[1] == []
    assert before[2] == []
    assert store.get_running_attempt(task_id) is None

    process = run_worker(store, task_id, "--resume")

    assert process.returncode == 1, process.stdout + process.stderr
    assert "RuntimeError" in process.stderr
    assert "NOT_ELIGIBLE:" not in process.stdout
    assert "SUCCEEDED result=" not in process.stdout
    assert saved_indices(process) == []
    assert read_snapshot(store, task_id) == before


def test_completion_rejected_is_not_reported_as_success(store):
    payload_text = "completion rejected"
    created = store.create_task("demo.echo", {"text": payload_text})
    task_id = created["task_id"]

    # 真实故障注入：仅跳过 Task 的 succeeded 更新，使完成方法返回 None。
    # queued -> running 不受影响；步骤仍正常执行、独立提交。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_reject_task_completion()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $test_trigger$
            BEGIN
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_reject_task_completion
            BEFORE UPDATE ON tasks
            FOR EACH ROW
            WHEN (NEW.status = 'succeeded')
            EXECUTE FUNCTION test_reject_task_completion()
        """)

    process = run_worker(store, task_id)

    assert process.returncode == 1, process.stdout + process.stderr
    assert "RuntimeError" in process.stderr
    assert "completion rejected" in process.stderr
    assert "SUCCEEDED result=" not in process.stdout
    assert "NOT_ELIGIBLE:" not in process.stdout
    assert saved_indices(process) == [0, 1, 2]

    task, attempts, steps = read_snapshot(store, task_id)
    assert task is not None
    assert task["status"] == "running"
    assert task["result"] is None
    assert len(attempts) == 1
    assert attempts[0]["attempt_no"] == 1
    assert attempts[0]["status"] == "running"
    assert attempts[0]["finished_at"] is None
    # Worker 的 RuntimeError 不会撤销此前已经提交的步骤。
    assert [step["step_index"] for step in steps] == [0, 1, 2]
    assert [step["step_name"] for step in steps] == STEP_NAMES
    assert [step["result"] for step in steps] == expected_outputs(payload_text)
    assert all(step["completed_at"] is not None for step in steps)
