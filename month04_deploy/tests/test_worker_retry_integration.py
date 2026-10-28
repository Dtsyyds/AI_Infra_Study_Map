"""真实 PostgreSQL：Worker 读取 Task 固定截止时间的重试验收（20 个用例）。

放置位置：month04_deploy/tests/test_worker_retry_integration.py
接口基准：create_task(retry_deadline_at=...) 保存截止时间，retry_task_once 不再接收它。

在项目根目录、已设置 AGENT_DATABASE_URL 的 agent-dev 环境运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -x -q month04_deploy/tests/test_worker_retry_integration.py

每例创建独立随机 schema，应用 001～005 迁移，结束后删除自己的 schema。
真实执行 TaskStore 的 SQL，不 mock 连接或领取/完成方法；用新连接核对提交结果。
monkeypatch 仅观察步骤调用及注入可控的步骤错误；崩溃用真实子进程验证。

范围：显式调用一次重试，不是自动调度。策略参数由测试明确提供；注入的
ValueError 只用于测试既有错误处理路径，不代表所有 ValueError 都允许重试。
复用检查点的前提是任务输入和步骤版本保持不变、已保存结果可继续使用。
默认截止时间在创建 Task 前用数据库时钟加 10 分钟生成，只在创建时写入。
崩溃实验子进程直接通过存储层读取 Task 中的截止时间，不从命令行接收它。
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable import worker
from month04_deploy.app.durable.retry_policy import RetryDecision
from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)

TEXT = "worker retry"
INJECTED_ERROR = "injected retryable step failure"
POLICY = {
    "retryable_error": True,
    "outcome_unknown": False,
    "replay_safe": True,
    "max_attempts": 3,
}
DEFAULT_DEADLINE = object()


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"
    assert callable(getattr(worker, "retry_task_once", None)), (
        "实际导入的 worker 缺少 retry_task_once，请先保存实现"
    )
    assert callable(getattr(TaskStore, "try_retry_task_with_attempt", None)), (
        "实际导入的 TaskStore 缺少 try_retry_task_with_attempt"
    )

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = [
        migrations_dir / "001_create_tasks.sql",
        migrations_dir / "002_create_task_steps.sql",
        migrations_dir / "003_add_task_idempotency.sql",
        migrations_dir / "004_create_task_attempts.sql",
        migrations_dir / "005_add_task_retry_deadline.sql",
    ]
    for migration in migrations:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    schema_name = f"worker_retry_test_{uuid4().hex}"
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
            for migration in migrations:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
        yield TaskStore(scoped_dsn)
    finally:
        with psycopg.connect(admin_dsn, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )


def read_state(store: TaskStore, task_id: UUID):
    """通过新连接读取完整记录，比较时包含全部错误、编号和时间戳。"""
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


def make_failed_task(
    store: TaskStore, saved_count: int = 1, *, retry_deadline_at=DEFAULT_DEADLINE
):
    """用真实存储接口准备失败 Task 和有效步骤前缀。

    saved_count=3 是人为准备的边界状态，用于验证无需重跑步骤也能完成新 Attempt。
    """
    created = create_task_for_retry(store, retry_deadline_at=retry_deadline_at)
    task_id = created["task_id"]
    claimed = store.try_start_task_with_attempt(task_id)
    assert isinstance(claimed, tuple) and len(claimed) == 2
    _, attempt = claimed
    state = created["payload"]
    for index, (name, operation) in enumerate(worker.STEPS[:saved_count]):
        state = operation(state)
        store.save_completed_step(task_id, index, name, state)
    failed = store.try_fail_task_with_attempt(
        task_id,
        attempt["attempt_id"],
        {"type": "InjectedTestFailure", "message": "fixture failure"},
    )
    assert failed is not None and failed["status"] == "failed"
    return task_id, attempt["attempt_id"]


def deadline_for_creation(store: TaskStore) -> datetime:
    """创建时生成固定截止时间，不在重试调用中重新生成预算。"""
    with psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as conn:
        now = conn.execute("SELECT clock_timestamp() AS checked_at").fetchone()["checked_at"]
    return now + timedelta(minutes=10)


def create_task_for_retry(store: TaskStore, *, retry_deadline_at=DEFAULT_DEADLINE):
    if retry_deadline_at is DEFAULT_DEADLINE:
        retry_deadline_at = deadline_for_creation(store)
    created = store.create_task(
        "demo.echo", {"text": TEXT}, retry_deadline_at=retry_deadline_at
    )
    assert created["retry_deadline_at"] == retry_deadline_at
    return created


def retry_arguments(**overrides):
    return {**POLICY, **overrides}


def retry(store: TaskStore, task_id: UUID, expected_attempt_id: UUID, **overrides):
    arguments = retry_arguments(**overrides)
    return worker.retry_task_once(store, task_id, expected_attempt_id, **arguments)


def observe_steps(monkeypatch, *, fail_once_at=None):
    """保留步骤名称和正常运算，只记录输入，按需让指定步骤第一次调用失败。"""
    calls = []
    injected = set()

    def wrap(index, operation):
        def observed(state):
            calls.append((index, deepcopy(state)))
            if index == fail_once_at and index not in injected:
                injected.add(index)
                raise ValueError(INJECTED_ERROR)
            return operation(state)
        return observed

    monkeypatch.setattr(
        worker,
        "STEPS",
        tuple(
            (name, wrap(index, operation))
            for index, (name, operation) in enumerate(worker.STEPS)
        ),
    )
    return calls


def forbid_steps(monkeypatch):
    def forbidden(state):
        raise AssertionError("拒绝重试时不应执行步骤")

    monkeypatch.setattr(
        worker, "STEPS", tuple((name, forbidden) for name, _ in worker.STEPS)
    )


def assert_succeeded_retry(store, task_id, finished, before):
    task, attempts, steps = read_state(store, task_id)
    assert finished is not None and task == finished
    assert task["status"] == "succeeded"
    assert task["result"] == {"answer": f"echo:{TEXT}"}
    assert task["error"] is None
    assert task["payload"] == before[0]["payload"]
    assert task["retry_deadline_at"] == before[0]["retry_deadline_at"]
    assert len(attempts) == 2
    assert attempts[0] == before[1][0]
    assert attempts[1]["attempt_id"] != attempts[0]["attempt_id"]
    assert attempts[1]["attempt_no"] == 2
    assert attempts[1]["status"] == "succeeded"
    assert attempts[1]["error"] is None
    assert attempts[1]["finished_at"] is not None
    assert steps[:len(before[2])] == before[2]
    assert [step["step_index"] for step in steps] == [0, 1, 2]
    assert [step["step_name"] for step in steps] == [name for name, _ in worker.STEPS]
    assert [step["result"] for step in steps] == [
        {"text": TEXT}, {"text": f"echo:{TEXT}"}, {"answer": f"echo:{TEXT}"}
    ]
    return task, attempts, steps


def assert_running_retry(store, task_id, before):
    task, attempts, steps = read_state(store, task_id)
    assert task == {
        **before[0],
        "status": "running",
        "result": None,
        "error": None,
        "updated_at": task["updated_at"],
    }
    assert len(attempts) == 2
    assert attempts[0] == before[1][0]
    assert attempts[1]["attempt_id"] != attempts[0]["attempt_id"]
    assert attempts[1]["attempt_no"] == 2
    assert attempts[1]["status"] == "running"
    assert attempts[1]["error"] is None
    assert attempts[1]["finished_at"] is None
    return task, attempts, steps


def test_failed_worker_retries_from_saved_input_with_new_attempt(store, monkeypatch):
    calls = observe_steps(monkeypatch, fail_once_at=1)
    created = create_task_for_retry(store)
    task_id = created["task_id"]
    with pytest.raises(ValueError, match=INJECTED_ERROR):
        worker.run_task_once(store, task_id)

    before = read_state(store, task_id)
    assert before[0]["status"] == "failed"
    assert len(before[1]) == 1 and before[1][0]["status"] == "failed"
    assert before[1][0]["error"] == before[0]["error"]
    assert before[0]["error"]["step_index"] == 1
    assert [step["step_index"] for step in before[2]] == [0]
    assert [index for index, _ in calls] == [0, 1]
    calls.clear()

    finished = retry(store, task_id, before[1][0]["attempt_id"])

    assert calls == [(1, before[2][0]["result"]), (2, {"text": f"echo:{TEXT}"})]
    assert_succeeded_retry(store, task_id, finished, before)


@pytest.mark.parametrize("saved_count", [0, 3], ids=["no-checkpoint", "all-steps-saved"])
def test_retry_handles_checkpoint_boundary(store, monkeypatch, saved_count):
    task_id, old_attempt_id = make_failed_task(store, saved_count)
    before = read_state(store, task_id)
    calls = observe_steps(monkeypatch)

    finished = retry(store, task_id, old_attempt_id)

    assert [index for index, _ in calls] == list(range(saved_count, len(worker.STEPS)))
    assert_succeeded_retry(store, task_id, finished, before)


@pytest.mark.parametrize(
    ("deadline_kind", "overrides", "expected"),
    [
        pytest.param("future", {"retryable_error": False}, RetryDecision.STOP, id="not-retryable"),
        pytest.param("future", {"max_attempts": 1}, RetryDecision.STOP, id="attempts-exhausted"),
        pytest.param(
            "expired", {},
            RetryDecision.STOP,
            id="deadline-expired",
        ),
        pytest.param(
            "future", {"outcome_unknown": True, "replay_safe": False},
            RetryDecision.RECONCILE,
            id="unknown-unsafe",
        ),
        pytest.param("null", {}, RetryDecision.STOP, id="null-deadline-stops"),
        pytest.param(
            "null", {"outcome_unknown": True, "replay_safe": False},
            RetryDecision.RECONCILE, id="null-deadline-still-reconciles",
        ),
        pytest.param(
            "expired",
            {
                "outcome_unknown": True,
                "replay_safe": False,
                "retryable_error": False,
                "max_attempts": 1,
            },
            RetryDecision.RECONCILE, id="expired-budget-still-reconciles",
        ),
    ],
)
def test_policy_refusal_returns_decision_without_execution_or_changes(
    store, monkeypatch, deadline_kind, overrides, expected
):
    if deadline_kind == "future":
        deadline = deadline_for_creation(store)
    elif deadline_kind == "expired":
        deadline = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        assert deadline_kind == "null"
        deadline = None
    task_id, old_attempt_id = make_failed_task(store, retry_deadline_at=deadline)
    before = read_state(store, task_id)
    forbid_steps(monkeypatch)

    decision = retry(store, task_id, old_attempt_id, **overrides)

    assert decision is expected
    assert read_state(store, task_id) == before


def test_retry_rejects_deadline_override_without_execution_or_changes(store, monkeypatch):
    task_id, old_attempt_id = make_failed_task(
        store, retry_deadline_at=datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    before = read_state(store, task_id)
    later_deadline = deadline_for_creation(store)
    forbid_steps(monkeypatch)

    with pytest.raises(TypeError, match="retry_deadline_at"):
        retry(store, task_id, old_attempt_id, retry_deadline_at=later_deadline)

    assert read_state(store, task_id) == before


@pytest.mark.parametrize("case", ["missing", "stale-attempt", "already-succeeded"])
def test_rejected_claim_returns_none_without_execution_or_changes(store, monkeypatch, case):
    if case == "missing":
        task_id, old_attempt_id = uuid4(), uuid4()
    else:
        task_id, old_attempt_id = make_failed_task(store)
        if case == "stale-attempt":
            claimed = store.try_retry_task_with_attempt(
                task_id, old_attempt_id, **retry_arguments()
            )
            assert isinstance(claimed, tuple) and len(claimed) == 2
            _, attempt = claimed
            failed = store.try_fail_task_with_attempt(
                task_id, attempt["attempt_id"], {"message": "second fixture failure"}
            )
            assert failed is not None and failed["status"] == "failed"
        else:
            finished = retry(store, task_id, old_attempt_id)
            assert finished is not None and finished["status"] == "succeeded"

    before = read_state(store, task_id)
    forbid_steps(monkeypatch)

    assert retry(store, task_id, old_attempt_id) is None
    assert read_state(store, task_id) == before


def test_retry_step_failure_is_saved_to_new_attempt_and_propagates(store, monkeypatch):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    calls = observe_steps(monkeypatch, fail_once_at=1)

    with pytest.raises(ValueError, match=INJECTED_ERROR):
        retry(store, task_id, old_attempt_id)

    task, attempts, steps = read_state(store, task_id)
    expected_error = {
        "type": "ValueError",
        "message": INJECTED_ERROR,
        "step_index": 1,
        "step_name": "build_echo_v1",
    }
    assert calls == [(1, before[2][0]["result"])]
    assert task["status"] == "failed" and task["result"] is None
    assert task["error"] == expected_error
    assert task["retry_deadline_at"] == before[0]["retry_deadline_at"]
    assert len(attempts) == 2 and attempts[0] == before[1][0]
    assert attempts[1]["attempt_id"] != old_attempt_id
    assert attempts[1]["attempt_no"] == 2 and attempts[1]["status"] == "failed"
    assert attempts[1]["error"] == expected_error
    assert attempts[1]["finished_at"] is not None
    assert steps == before[2]


def test_step_save_sql_error_keeps_committed_retry_running(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "ALTER TABLE task_steps ADD CONSTRAINT test_reject_retry_step "
            "CHECK (step_index < 1)"
        )

    with pytest.raises(psycopg.errors.CheckViolation, match="test_reject_retry_step"):
        retry(store, task_id, old_attempt_id)

    _, _, steps = assert_running_retry(store, task_id, before)
    assert steps == before[2]


def test_rejected_completion_raises_and_does_not_report_retry_success(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            CREATE FUNCTION test_reject_retry_completion()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.status = 'succeeded' THEN
                    RETURN NULL;
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER test_reject_retry_completion
            BEFORE UPDATE ON tasks FOR EACH ROW
            EXECUTE FUNCTION test_reject_retry_completion();
            """,
            prepare=False,
        )

    with pytest.raises(RuntimeError, match="completion rejected"):
        retry(store, task_id, old_attempt_id)

    _, _, steps = assert_running_retry(store, task_id, before)
    assert steps[:1] == before[2]
    assert [step["step_index"] for step in steps] == [0, 1, 2]
    assert steps[-1]["result"] == {"answer": f"echo:{TEXT}"}


@pytest.mark.parametrize("crash_after_step", [-1, 3])
def test_invalid_crash_argument_is_rejected_before_retry_claim(store, crash_after_step):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)

    with pytest.raises(ValueError, match="crash_after_step"):
        retry(store, task_id, old_attempt_id, crash_after_step=crash_after_step)

    assert read_state(store, task_id) == before


def test_crashed_retry_is_manually_resumed_with_same_new_attempt(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    project_root = Path(__file__).resolve().parents[2]
    program = """
import os
import sys
from uuid import UUID
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.app.durable.worker import retry_task_once

retry_task_once(
    TaskStore(os.environ["AGENT_DATABASE_URL"]),
    UUID(sys.argv[1]),
    UUID(sys.argv[2]),
    retryable_error=True,
    outcome_unknown=False,
    replay_safe=True,
    max_attempts=3,
    crash_after_step=1,
)
raise AssertionError("预期在步骤 1 提交后以退出码 86 崩溃")
"""
    env = os.environ.copy()
    env["AGENT_DATABASE_URL"] = store.dsn
    process = subprocess.run(
        [
            sys.executable, "-c", program, str(task_id), str(old_attempt_id),
        ],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 86, process.stdout + process.stderr
    assert "STEP_SAVED index=1" in process.stdout
    assert "STEP_SAVED index=0" not in process.stdout
    _, crashed_attempts, crashed_steps = assert_running_retry(store, task_id, before)
    assert crashed_steps[:1] == before[2]
    assert [step["step_index"] for step in crashed_steps] == [0, 1]

    # subprocess.run 已等待旧进程退出；此用例没有其他 Worker，满足人工恢复前提。
    finished = worker.run_task_once(store, task_id, resume=True)

    _, attempts, steps = assert_succeeded_retry(store, task_id, finished, before)
    assert attempts[1]["attempt_id"] == crashed_attempts[1]["attempt_id"]
    assert attempts[1]["started_at"] == crashed_attempts[1]["started_at"]
    assert steps[:2] == crashed_steps
