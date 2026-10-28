"""真实 PostgreSQL：读取 Task 固定截止时间的重试领取验收（30 个用例）。

放置位置：month04_deploy/tests/test_task_retry_integration.py
接口基准：create_task(retry_deadline_at=...) 保存截止时间；重试入口不再接收它。
实现依据：本轮上传的 TaskStore；迁移 005 为 Task 添加可空的截止时间列。

在项目根目录、已设置 AGENT_DATABASE_URL 的 agent-dev 环境中运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short \
        -q month04_deploy/tests/test_task_retry_integration.py

每个用例使用独立随机 schema，应用 001～005 迁移，结束后删除自己的 schema。
故障注入只作用于该 schema；不 mock 连接、SQL 或 fetchone。
提交或回滚后用新连接读取完整 Task、Attempt 和步骤记录。

范围：只验收存储层重试领取，不代表 Worker 已接入自动重试。
策略条件由测试显式提供；Attempt 从 1 连续编号且历史不删除。
默认截止时间在创建 Task 前用数据库时钟加 10 分钟生成，只在创建时写入。
重试辅助函数不读取、生成或改写截止时间；NULL 表示没有重试预算。
新增真实行锁等待测试，用 pg_blocking_pids 确认阻塞已发生，再等待截止时间。
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.app.durable.retry_policy import RetryDecision
from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)

DEFAULT_DEADLINE = object()


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"
    assert callable(getattr(TaskStore, "try_retry_task_with_attempt", None)), (
        "实际导入的 TaskStore 缺少 try_retry_task_with_attempt，请先保存实现"
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

    schema_name = f"task_retry_test_{uuid4().hex}"
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
    """新连接回读完整记录；比较时包含错误内容、编号与全部时间戳。"""
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


def assert_claimed(value):
    assert isinstance(value, tuple), f"预期已提交的领取二元组，实际为 {value!r}"
    assert len(value) == 2
    return value


def make_failed_task(
    store: TaskStore, *, retry_deadline_at=DEFAULT_DEADLINE
) -> tuple[UUID, UUID]:
    """用已实现的接口准备一次失败，保留一个已提交的步骤。"""
    if retry_deadline_at is DEFAULT_DEADLINE:
        retry_deadline_at = deadline_for_creation(store)
    created = store.create_task(
        "demo.echo", {"text": "retry acceptance"}, retry_deadline_at=retry_deadline_at
    )
    assert created["retry_deadline_at"] == retry_deadline_at
    task_id = created["task_id"]
    _, attempt = assert_claimed(store.try_start_task_with_attempt(task_id))
    store.save_completed_step(
        task_id, 0, "read_input_v1", {"text": "retry acceptance"}
    )
    error = {
        "type": "TransientTestError",
        "message": "测试注入的失败；是否重试由本用例的策略参数决定",
        "step_index": 1,
        "step_name": "build_echo_v1",
    }
    failed = store.try_fail_task_with_attempt(task_id, attempt["attempt_id"], error)
    assert failed is not None and failed["status"] == "failed"
    saved_task, attempts, steps = read_state(store, task_id)
    assert saved_task == failed
    assert len(attempts) == 1 and attempts[0]["status"] == "failed"
    assert attempts[0]["attempt_no"] == 1
    assert attempts[0]["error"] == error
    assert attempts[0]["finished_at"] is not None
    assert len(steps) == 1
    return task_id, attempt["attempt_id"]


def deadline_for_creation(store: TaskStore, *, seconds: int = 600) -> datetime:
    """只在创建任务前生成固定截止时间，使用数据库时钟避免跨主机时钟偏差。"""
    with psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as conn:
        now = conn.execute("SELECT clock_timestamp() AS checked_at").fetchone()["checked_at"]
    return now + timedelta(seconds=seconds)


def retry(store: TaskStore, task_id: UUID, expected_attempt_id: UUID, **overrides):
    policy = {
        "retryable_error": True,
        "outcome_unknown": False,
        "replay_safe": True,
        "max_attempts": 3,
    }
    policy.update(overrides)
    return store.try_retry_task_with_attempt(task_id, expected_attempt_id, **policy)


@pytest.mark.parametrize("outcome_unknown", [False, True], ids=["known", "unknown-safe"])
def test_retry_commits_task_and_new_attempt_preserves_history(store, outcome_unknown):
    task_id, old_attempt_id = make_failed_task(store)
    # 人工设置旧 result，验证重试确实将其清空；不是正常 Worker 的输出。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "UPDATE tasks SET result = %s WHERE task_id = %s",
            (Jsonb({"stale_result": True}), task_id),
        )
    before_task, before_attempts, before_steps = read_state(store, task_id)

    task, attempt = assert_claimed(
        retry(store, task_id, old_attempt_id, outcome_unknown=outcome_unknown)
    )

    assert task == {
        **before_task,
        "status": "running",
        "result": None,
        "error": None,
        "updated_at": task["updated_at"],
    }
    assert task["updated_at"] >= before_task["updated_at"]
    assert attempt["attempt_id"] != old_attempt_id
    assert attempt["task_id"] == task_id
    assert attempt["attempt_no"] == 2
    assert attempt["status"] == "running"
    assert attempt["error"] is None
    assert attempt["finished_at"] is None
    assert attempt["started_at"] >= before_attempts[0]["finished_at"]
    # 完整比较：旧 Attempt（包含错误和结束时间）与步骤均没有改动。
    assert read_state(store, task_id) == (
        task, [*before_attempts, attempt], before_steps
    )


def test_retried_attempt_can_succeed_without_rewriting_old_failure(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    _, attempt = assert_claimed(retry(store, task_id, old_attempt_id))

    result = {"answer": "echo:retry acceptance"}
    finished = store.try_succeed_task_with_attempt(task_id, attempt["attempt_id"], result)

    task, attempts, steps = read_state(store, task_id)
    assert finished is not None and task == finished
    assert task["status"] == "succeeded"
    assert task["result"] == result
    assert task["error"] is None
    assert task["retry_deadline_at"] == before[0]["retry_deadline_at"]
    assert len(attempts) == 2
    assert attempts[0] == before[1][0]
    assert attempts[1]["finished_at"] is not None
    assert attempts[1] == {
        **attempt,
        "status": "succeeded",
        "finished_at": attempts[1]["finished_at"],
    }
    assert steps == before[2]


@pytest.mark.parametrize(
    ("deadline_kind", "overrides", "expected"),
    [
        pytest.param("future", {"retryable_error": False}, RetryDecision.STOP, id="non-retryable"),
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
        pytest.param(
            "expired",
            {
                "outcome_unknown": True,
                "replay_safe": False,
                "retryable_error": False,
                "max_attempts": 1,
            },
            RetryDecision.RECONCILE,
            id="reconcile-before-stop",
        ),
        pytest.param("null", {}, RetryDecision.STOP, id="null-deadline-stops"),
        pytest.param(
            "null", {"outcome_unknown": True, "replay_safe": False},
            RetryDecision.RECONCILE, id="null-deadline-still-reconciles",
        ),
    ],
)
def test_policy_rejection_preserves_all_rows(store, deadline_kind, overrides, expected):
    if deadline_kind == "future":
        deadline = deadline_for_creation(store)
    elif deadline_kind == "expired":
        deadline = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        assert deadline_kind == "null"
        deadline = None
    task_id, old_attempt_id = make_failed_task(store, retry_deadline_at=deadline)
    before = read_state(store, task_id)

    assert retry(store, task_id, old_attempt_id, **overrides) is expected

    assert read_state(store, task_id) == before


def test_repeated_request_is_rejected_after_retry_claim(store):
    task_id, old_attempt_id = make_failed_task(store)
    assert_claimed(retry(store, task_id, old_attempt_id))
    before = read_state(store, task_id)

    assert retry(store, task_id, old_attempt_id) is None

    assert read_state(store, task_id) == before


def test_stale_request_cannot_retry_newer_failure(store):
    task_id, first_attempt_id = make_failed_task(store)
    original_deadline = read_state(store, task_id)[0]["retry_deadline_at"]
    second_task, second_attempt = assert_claimed(retry(store, task_id, first_attempt_id))
    assert second_task["retry_deadline_at"] == original_deadline
    second_error = {"type": "TransientTestError", "message": "second failure"}
    assert store.try_fail_task_with_attempt(
        task_id, second_attempt["attempt_id"], second_error
    ) is not None
    before = read_state(store, task_id)
    assert before[0]["status"] == "failed"
    assert before[0]["retry_deadline_at"] == original_deadline
    assert [a["attempt_no"] for a in before[1]] == [1, 2]

    # Task 再次 failed，但针对第一次失败的旧请求不能领取第三次执行。
    assert retry(store, task_id, first_attempt_id) is None
    assert read_state(store, task_id) == before

    task, third_attempt = assert_claimed(
        retry(store, task_id, second_attempt["attempt_id"])
    )
    assert third_attempt["attempt_no"] == 3
    assert third_attempt["status"] == "running"
    assert task["retry_deadline_at"] == original_deadline
    assert read_state(store, task_id) == (
        task, [*before[1], third_attempt], before[2]
    )


@pytest.mark.parametrize("kind", ["missing", "other-task"])
def test_wrong_expected_attempt_returns_none(store, kind):
    task_id, _ = make_failed_task(store)
    other_task_id, other_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    before_other = read_state(store, other_task_id)
    wrong_id = uuid4() if kind == "missing" else other_attempt_id

    assert retry(store, task_id, wrong_id) is None

    assert read_state(store, task_id) == before
    assert read_state(store, other_task_id) == before_other


@pytest.mark.parametrize("state", ["missing", "queued", "running", "succeeded", "cancelled"])
def test_non_failed_or_missing_task_returns_none(store, state):
    task_id, expected_id = uuid4(), uuid4()
    if state != "missing":
        task_id = store.create_task("demo.echo", {"text": "state guard"})["task_id"]
        if state in ("running", "succeeded"):
            _, attempt = assert_claimed(store.try_start_task_with_attempt(task_id))
            expected_id = attempt["attempt_id"]
            if state == "succeeded":
                assert store.try_succeed_task_with_attempt(
                    task_id, expected_id, {"answer": "done"}
                ) is not None
        elif state == "cancelled":
            with psycopg.connect(store.dsn, connect_timeout=5) as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'cancelled' WHERE task_id = %s",
                    (task_id,),
                )
    before = read_state(store, task_id)

    assert retry(store, task_id, expected_id) is None

    assert read_state(store, task_id) == before


def test_failed_task_without_attempt_raises_without_changes(store):
    task_id = store.create_task("demo.echo", {"text": "inconsistent fixture"})["task_id"]
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("UPDATE tasks SET status = 'failed' WHERE task_id = %s", (task_id,))
    before = read_state(store, task_id)

    with pytest.raises(RuntimeError, match="无最新 Attempt 记录"):
        retry(store, task_id, uuid4())

    assert read_state(store, task_id) == before


def test_latest_attempt_must_not_be_selected_by_failed_status(store):
    task_id, first_attempt_id = make_failed_task(store)
    # 故意构造不一致：Task failed，但最新 Attempt 已 succeeded。
    # 若查询先筛 failed，会错误地跳过第二条，退回到第一次失败。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO task_attempts (attempt_id, task_id, attempt_no, status, finished_at)
            VALUES (%s, %s, 2, 'succeeded', CURRENT_TIMESTAMP)
            """,
            (uuid4(), task_id),
        )
    before = read_state(store, task_id)

    with pytest.raises(RuntimeError, match="最新 Attempt 不是 failed"):
        retry(store, task_id, first_attempt_id)

    assert read_state(store, task_id) == before


def test_attempt_insert_sql_error_rolls_back_task_retry(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            ALTER TABLE task_attempts
            ADD CONSTRAINT test_reject_retry_attempt CHECK (attempt_no < 2)
        """)

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        retry(store, task_id, old_attempt_id)

    assert caught.value.diag.table_name == "task_attempts"
    assert caught.value.diag.constraint_name == "test_reject_retry_attempt"
    assert read_state(store, task_id) == before


def test_attempt_insert_zero_rows_raises_and_rolls_back_task_retry(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_skip_retry_insert()
            RETURNS trigger LANGUAGE plpgsql AS $test_trigger$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM tasks
                    WHERE task_id = NEW.task_id
                      AND status = 'running'
                      AND result IS NULL
                      AND error IS NULL
                ) THEN
                    RAISE EXCEPTION 'test expected Task retry update first';
                END IF;
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_skip_retry_insert
            BEFORE INSERT ON task_attempts
            FOR EACH ROW WHEN (NEW.attempt_no > 1)
            EXECUTE FUNCTION test_skip_retry_insert()
        """)

    # SQL 本身成功，但 BEFORE INSERT 返回 NULL，使 RETURNING 为零行。
    # 改成 return None 会提交 running Task；这个用例应抓住该回归。
    with pytest.raises(RuntimeError, match="创建重试 Attempt 后没有返回记录"):
        retry(store, task_id, old_attempt_id)

    assert read_state(store, task_id) == before


def test_task_update_zero_rows_raises_without_creating_attempt(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_skip_task_retry()
            RETURNS trigger LANGUAGE plpgsql AS $test_trigger$
            BEGIN
                RETURN NULL;
            END;
            $test_trigger$
        """)
        conn.execute("""
            CREATE TRIGGER test_skip_task_retry
            BEFORE UPDATE ON tasks
            FOR EACH ROW WHEN (OLD.status = 'failed' AND NEW.status = 'running')
            EXECUTE FUNCTION test_skip_task_retry()
        """)

    with pytest.raises(RuntimeError, match="重试更新 Task 后没有返回记录"):
        retry(store, task_id, old_attempt_id)

    assert read_state(store, task_id) == before


def test_two_concurrent_retry_requests_only_one_claims(store):
    task_id, old_attempt_id = make_failed_task(store)
    before = read_state(store, task_id)
    barrier = Barrier(2)

    def contender():
        independent_store = TaskStore(store.dsn)
        barrier.wait(timeout=10)
        return retry(independent_store, task_id, old_attempt_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(contender) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    accepted = [value for value in results if isinstance(value, tuple)]
    assert len(accepted) == 1
    assert sum(value is None for value in results) == 1
    task, attempt = assert_claimed(accepted[0])
    assert task["status"] == "running"
    assert attempt["attempt_no"] == 2
    assert attempt["status"] == "running"
    assert read_state(store, task_id) == (
        task, [*before[1], attempt], before[2]
    )


def test_retry_cannot_override_expired_persisted_deadline(store):
    task_id, old_attempt_id = make_failed_task(
        store, retry_deadline_at=datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    before = read_state(store, task_id)
    later_deadline = deadline_for_creation(store)

    # 新接口拒绝在重试调用时传入另一个截止时间，不能借此重新获得预算。
    with pytest.raises(TypeError, match="retry_deadline_at"):
        retry(store, task_id, old_attempt_id, retry_deadline_at=later_deadline)

    assert read_state(store, task_id) == before
    assert retry(store, task_id, old_attempt_id) is RetryDecision.STOP
    assert read_state(store, task_id) == before


@pytest.mark.parametrize("offset_hours", [8, -7], ids=["utc-plus-8", "utc-minus-7"])
def test_aware_deadline_accepts_other_timezone_offsets(store, offset_hours):
    deadline = deadline_for_creation(store)
    same_instant = deadline.astimezone(timezone(timedelta(hours=offset_hours)))
    assert same_instant == deadline
    task_id, old_attempt_id = make_failed_task(store, retry_deadline_at=same_instant)
    before = read_state(store, task_id)
    assert before[0]["retry_deadline_at"] == deadline

    task, attempt = assert_claimed(retry(store, task_id, old_attempt_id))

    assert task["status"] == "running"
    assert attempt["attempt_no"] == 2 and attempt["status"] == "running"
    assert task["retry_deadline_at"] == deadline
    assert read_state(store, task_id) == (
        task, [*before[1], attempt], before[2]
    )


def database_clock(connection) -> datetime:
    return connection.execute(
        "SELECT clock_timestamp() AS checked_at"
    ).fetchone()["checked_at"]


def wait_for_task_row_block(monitor, application_name, blocker_pid, future):
    """观察真实阻塞关系；不能仅凭启动了线程或 sleep 推定它已经在等锁。"""
    timeout_at = monotonic() + 4
    while monotonic() < timeout_at:
        if future.done():
            # 先传播实现异常；若正常返回，则明确报告没有观察到预期的行锁等待。
            value = future.result()
            pytest.fail(f"重试调用未保持等待 Task 行锁就结束：{value!r}")
        row = monitor.execute(
            """
            SELECT pid, xact_start, wait_event_type
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND application_name = %s
              AND %s = ANY(pg_blocking_pids(pid))
            """,
            (application_name, blocker_pid),
        ).fetchone()
        if row is not None:
            assert row["wait_event_type"] == "Lock"
            assert row["xact_start"] is not None
            return row
        sleep(0.05)
    pytest.fail("4 秒内未观察到测试重试连接被 Task 行锁阻塞，请检查测试环境")


@pytest.mark.parametrize(
    "expires_while_waiting", [True, False],
    ids=["expires-while-waiting", "still-open-after-wait"],
)
def test_retry_rechecks_actual_time_after_task_lock_wait(store, expires_while_waiting):
    # 截止时间必须在创建时提交；重试线程只知道 task_id 和旧 attempt_id。
    retry_deadline_at = deadline_for_creation(
        store, seconds=8 if expires_while_waiting else 600
    )
    task_id, old_attempt_id = make_failed_task(store, retry_deadline_at=retry_deadline_at)
    before = read_state(store, task_id)
    assert before[0]["retry_deadline_at"] == retry_deadline_at
    application_name = f"retry_deadline_wait_{uuid4().hex}"
    # 保留 schema 等已有连接选项，只为本例延长两个查询超时，容纳 8 秒预算。
    options = conninfo_to_dict(store.dsn).get("options", "")
    contender_store = TaskStore(make_conninfo(
        store.dsn,
        application_name=application_name,
        options=f"{options} -c lock_timeout=15000 -c statement_timeout=20000",
    ))

    with (
        psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as blocker,
        psycopg.connect(
            store.dsn, row_factory=dict_row, connect_timeout=5, autocommit=True
        ) as monitor,
    ):
        blocker_pid = blocker.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
        locked_task = blocker.execute(
            "SELECT * FROM tasks WHERE task_id = %s FOR UPDATE", (task_id,)
        ).fetchone()
        assert locked_task == before[0]

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                retry,
                contender_store,
                task_id,
                old_attempt_id,
            )
            try:
                waiting = wait_for_task_row_block(
                    monitor, application_name, blocker_pid, future
                )
                assert waiting["xact_start"] < retry_deadline_at
                assert database_clock(monitor) < retry_deadline_at, (
                    "尚未在截止时间前完成阻塞确认，不能据此证明等待锁导致预算过期"
                )

                if expires_while_waiting:
                    timeout_at = monotonic() + 12
                    while database_clock(monitor) < retry_deadline_at:
                        assert monotonic() < timeout_at, "数据库时钟未在预期范围内到达截止时间"
                        assert not future.done(), "行锁释放前，重试调用不应结束"
                        sleep(0.05)
                    assert database_clock(monitor) >= retry_deadline_at
                else:
                    assert database_clock(monitor) < retry_deadline_at
            finally:
                # 必须在等待线程退出之前释放锁；断言失败时也按这个顺序清理。
                blocker.rollback()

            outcome = future.result(timeout=10)

    if expires_while_waiting:
        assert outcome is RetryDecision.STOP
        # 未更新 Task，未创建 Attempt 2，旧错误、时间戳和步骤都保持不变。
        assert read_state(store, task_id) == before
    else:
        task, attempt = assert_claimed(outcome)
        assert task["status"] == "running"
        assert attempt["attempt_no"] == 2 and attempt["status"] == "running"
        assert read_state(store, task_id) == (
            task, [*before[1], attempt], before[2]
        )
