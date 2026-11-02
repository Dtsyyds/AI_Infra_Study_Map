"""真实 PostgreSQL：同库工具调用账本的幂等性与事务验收。

运行位置：仓库根目录；沿用 AGENT_DATABASE_URL。
AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
    -p pytest_asyncio.plugin --tb=short -x -q \
    month04_deploy/tests/test_tool_call_idempotency_integration.py

每个用例使用独立随机 schema，按顺序加载 001～008 迁移并在结束后清理。
通过新连接读取提交后的完整记录；故障注入只作用于当前测试 schema。
子进程测试调用 TaskStore，退出后由父进程显式标记失败、安排并领取重试。
本文件验收存储层工具事务；Worker 的工具接入与自动恢复另行验收。
"""

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)

COUNTER_NAME = "ledger-acceptance-counter"


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"
    assert callable(getattr(TaskStore, "try_increment_counter_once", None)), (
        "实际导入的 TaskStore 缺少 try_increment_counter_once"
    )

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    names = (
        "001_create_tasks.sql",
        "002_create_task_steps.sql",
        "003_add_task_idempotency.sql",
        "004_create_task_attempts.sql",
        "005_add_task_retry_deadline.sql",
        "006_add_task_max_attempts.sql",
        "007_add_task_next_retry_at.sql",
        "008_create_tool_calls.sql",
    )
    migrations = [migrations_dir / name for name in names]
    for migration in migrations:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    schema_name = f"tool_ledger_test_{uuid4().hex}"
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


def read_state(store):
    """在新连接中回读，包括时间戳、错误、历史 Attempt 和全部账本。"""
    with psycopg.connect(
        store.dsn, row_factory=dict_row, connect_timeout=5
    ) as conn:
        return {
            "tasks": conn.execute(
                "SELECT * FROM tasks ORDER BY task_id"
            ).fetchall(),
            "attempts": conn.execute(
                "SELECT * FROM task_attempts ORDER BY task_id, attempt_no"
            ).fetchall(),
            "steps": conn.execute(
                "SELECT * FROM task_steps ORDER BY task_id, step_index"
            ).fetchall(),
            "counters": conn.execute(
                "SELECT * FROM demo_counters ORDER BY counter_name"
            ).fetchall(),
            "calls": conn.execute(
                "SELECT * FROM tool_calls ORDER BY task_id, step_index, call_index"
            ).fetchall(),
        }


def assert_execution_unchanged(before, after):
    for key in ("tasks", "attempts", "steps"):
        assert after[key] == before[key], f"工具调用意外修改了 {key}"


def create_task(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    return store.create_task(
        "demo.echo",
        {"text": "tool ledger acceptance"},
        retry_deadline_at=now + timedelta(seconds=600),
        max_attempts=3,
    )


def assert_claimed(value):
    assert isinstance(value, tuple) and len(value) == 2, (
        f"预期已提交的 Task/Attempt 二元组，实际为 {value!r}"
    )
    task, attempt = value
    assert task["status"] == "running"
    assert attempt["status"] == "running"
    assert attempt["task_id"] == task["task_id"]
    return task, attempt


def make_running_task(store):
    created = create_task(store)
    task, attempt = assert_claimed(
        store.try_start_task_with_attempt(created["task_id"])
    )
    return task["task_id"], attempt["attempt_id"]


def seed_counter(store, value):
    if value is not None:
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute(
                "INSERT INTO demo_counters (counter_name, value) VALUES (%s, %s)",
                (COUNTER_NAME, value),
            )


def increment(
    store, task_id, attempt_id, *,
    step_index=0, call_index=0, counter_name=COUNTER_NAME,
):
    """仅调用工具方法；这里不隐式领取 Task，也不安排重试。"""
    return store.try_increment_counter_once(
        task_id,
        attempt_id,
        step_index=step_index,
        call_index=call_index,
        counter_name=counter_name,
    )


def assert_call(row, task_id, result, *, step_index=0, call_index=0):
    assert isinstance(row["tool_call_id"], UUID)
    assert row["task_id"] == task_id
    assert row["step_index"] == step_index
    assert row["call_index"] == call_index
    assert row["tool_name"] == "demo.counter_increment_v1"
    assert row["input_fingerprint"]
    assert row["result"] == result
    assert row["completed_at"] is not None


@pytest.mark.parametrize("initial", [None, 7], ids=["new-counter", "existing-counter"])
def test_first_call_commits_and_repeated_call_preserves_original_record(store, initial):
    task_id, attempt_id = make_running_task(store)
    seed_counter(store, initial)
    before = read_state(store)
    expected = {"counter_name": COUNTER_NAME, "value": 1 if initial is None else 8}

    assert increment(store, task_id, attempt_id) == expected

    committed = read_state(store)
    assert_execution_unchanged(before, committed)
    assert committed["counters"] == [expected]
    assert len(committed["calls"]) == 1
    assert_call(committed["calls"][0], task_id, expected)
    assert committed["steps"] == []

    for _ in range(2):
        assert increment(store, task_id, attempt_id) == expected
        assert read_state(store) == committed


@pytest.mark.parametrize(
    "second_position", [(0, 1), (1, 0)],
    ids=["another-call-in-same-step", "another-step"],
)
def test_independent_logical_positions_each_execute_once(store, second_position):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)
    first = {"counter_name": COUNTER_NAME, "value": 1}
    second = {"counter_name": COUNTER_NAME, "value": 2}
    step_index, call_index = second_position

    assert increment(store, task_id, attempt_id) == first
    assert increment(
        store, task_id, attempt_id, step_index=step_index, call_index=call_index
    ) == second

    committed = read_state(store)
    assert_execution_unchanged(before, committed)
    assert committed["counters"] == [second]
    assert len(committed["calls"]) == 2
    assert len({row["tool_call_id"] for row in committed["calls"]}) == 2
    by_position = {
        (row["step_index"], row["call_index"]): row for row in committed["calls"]
    }
    assert_call(by_position[(0, 0)], task_id, first)
    assert_call(
        by_position[second_position], task_id, second,
        step_index=step_index, call_index=call_index,
    )

    assert increment(store, task_id, attempt_id) == first
    assert increment(
        store, task_id, attempt_id, step_index=step_index, call_index=call_index
    ) == second
    assert read_state(store) == committed


def test_other_task_same_position_is_independent_and_replay_returns_saved_result(store):
    first_id, first_attempt = make_running_task(store)
    second_id, second_attempt = make_running_task(store)
    seed_counter(store, 7)
    before = read_state(store)
    first = {"counter_name": COUNTER_NAME, "value": 8}
    second = {"counter_name": COUNTER_NAME, "value": 9}

    assert increment(store, first_id, first_attempt) == first
    assert increment(store, second_id, second_attempt) == second

    committed = read_state(store)
    assert_execution_unchanged(before, committed)
    assert committed["counters"] == [second]
    assert len(committed["calls"]) == 2
    assert len({row["tool_call_id"] for row in committed["calls"]}) == 2
    by_task = {row["task_id"]: row for row in committed["calls"]}
    assert_call(by_task[first_id], first_id, first)
    assert_call(by_task[second_id], second_id, second)

    # 当前计数器已是 9，第一次调用仍应返回当时保存的 8。
    assert increment(store, first_id, first_attempt) == first
    assert read_state(store) == committed


@pytest.mark.parametrize("changed", ["counter-name", "tool-name"])
def test_same_logical_position_rejects_changed_operation_without_any_write(store, changed):
    task_id, attempt_id = make_running_task(store)
    increment(store, task_id, attempt_id)
    requested_name = COUNTER_NAME
    if changed == "counter-name":
        requested_name = "another-counter"
    else:
        # 模拟此逻辑位置曾由另一工具版本执行；保留指纹以单独检验工具名。
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute(
                "UPDATE tool_calls SET tool_name = %s WHERE task_id = %s",
                ("demo.counter_increment_v2", task_id),
            )
    before = read_state(store)

    with pytest.raises(ValueError, match="同一工具调用的名称或参数发生变化"):
        increment(store, task_id, attempt_id, counter_name=requested_name)

    assert read_state(store) == before


def test_process_exit_after_tool_commit_then_new_attempt_replays_without_increment(store):
    task_id, first_attempt_id = make_running_task(store)
    seed_counter(store, 7)
    before = read_state(store)
    expected = {"counter_name": COUNTER_NAME, "value": 8}
    child_code = textwrap.dedent("""
        import os
        import sys
        from uuid import UUID
        from month04_deploy.app.durable.task_store import TaskStore

        store = TaskStore(os.environ["AGENT_TOOL_LEDGER_TEST_DSN"])
        result = store.try_increment_counter_once(
            UUID(sys.argv[1]),
            UUID(sys.argv[2]),
            step_index=0,
            call_index=0,
            counter_name=sys.argv[3],
        )
        assert result == {"counter_name": sys.argv[3], "value": 8}, result
        # 工具事务已经提交；进程退出前没有保存步骤检查点或结束 Attempt。
        os._exit(86)
    """)
    completed = subprocess.run(
        [sys.executable, "-c", child_code, str(task_id),
         str(first_attempt_id), COUNTER_NAME],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "AGENT_TOOL_LEDGER_TEST_DSN": store.dsn},
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert completed.returncode == 86, (
        f"子进程未到达预定退出点：code={completed.returncode}\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    after_exit = read_state(store)
    assert_execution_unchanged(before, after_exit)
    assert after_exit["steps"] == []
    assert after_exit["counters"] == [expected]
    assert len(after_exit["calls"]) == 1
    assert_call(after_exit["calls"][0], task_id, expected)

    # 子进程已确认退出，测试显式结束旧 Attempt，再使用已实现的重试接口。
    failed = store.try_fail_task_with_attempt(
        task_id, first_attempt_id,
        {"type": "InjectedProcessExit", "message": "tool committed before checkpoint"},
    )
    assert failed is not None and failed["status"] == "failed"
    policy = {
        "retryable_error": True,
        "outcome_unknown": True,
        "replay_safe": True,
    }
    scheduled = store.try_schedule_task_retry(
        task_id, first_attempt_id, delay_seconds=0, **policy
    )
    assert isinstance(scheduled, dict)
    assert scheduled["next_retry_at"] is not None
    _, second_attempt = assert_claimed(
        store.try_retry_task_with_attempt(task_id, first_attempt_id, **policy)
    )
    assert second_attempt["attempt_no"] == 2
    assert second_attempt["attempt_id"] != first_attempt_id

    before_replay = read_state(store)
    assert before_replay["calls"] == after_exit["calls"]
    assert before_replay["counters"] == after_exit["counters"]

    # 旧 Attempt 对已有和新的逻辑位置都没有执行资格。
    assert increment(store, task_id, first_attempt_id) is None
    assert increment(store, task_id, first_attempt_id, call_index=1) is None
    assert read_state(store) == before_replay

    assert increment(store, task_id, second_attempt["attempt_id"]) == expected
    assert read_state(store) == before_replay


def test_concurrent_duplicate_calls_both_return_one_committed_result(store):
    task_id, attempt_id = make_running_task(store)
    seed_counter(store, 7)
    before = read_state(store)
    barrier = Barrier(2)

    def contender():
        independent_store = TaskStore(store.dsn)
        barrier.wait(timeout=10)
        return increment(independent_store, task_id, attempt_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(contender) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    expected = {"counter_name": COUNTER_NAME, "value": 8}
    assert results == [expected, expected]
    committed = read_state(store)
    assert_execution_unchanged(before, committed)
    assert committed["counters"] == [expected]
    assert len(committed["calls"]) == 1
    assert_call(committed["calls"][0], task_id, expected)


@pytest.mark.parametrize("initial", [None, 7], ids=["new-counter", "existing-counter"])
@pytest.mark.parametrize("fault", ["sql-error", "zero-row"])
def test_ledger_failure_rolls_back_counter_and_allows_clean_retry(store, initial, fault):
    task_id, attempt_id = make_running_task(store)
    seed_counter(store, initial)
    before = read_state(store)

    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        if fault == "sql-error":
            conn.execute("""
                ALTER TABLE tool_calls
                ADD CONSTRAINT test_reject_tool_call CHECK (call_index <> 0)
            """)
        else:
            conn.execute("""
                CREATE FUNCTION test_skip_tool_call_insert()
                RETURNS trigger LANGUAGE plpgsql AS $test_trigger$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM demo_counters
                        WHERE counter_name = (NEW.result ->> 'counter_name')
                          AND value = (NEW.result ->> 'value')::BIGINT
                    ) THEN
                        RAISE EXCEPTION 'test expected counter update before ledger insert';
                    END IF;
                    RETURN NULL;
                END;
                $test_trigger$
            """)
            conn.execute("""
                CREATE TRIGGER test_skip_tool_call_insert
                BEFORE INSERT ON tool_calls
                FOR EACH ROW EXECUTE FUNCTION test_skip_tool_call_insert()
            """)

    if fault == "sql-error":
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            increment(store, task_id, attempt_id)
        assert caught.value.diag.table_name == "tool_calls"
        assert caught.value.diag.constraint_name == "test_reject_tool_call"
    else:
        with pytest.raises(RuntimeError, match="写入工具账本后没有返回记录"):
            increment(store, task_id, attempt_id)

    assert read_state(store) == before

    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        if fault == "sql-error":
            conn.execute(
                "ALTER TABLE tool_calls DROP CONSTRAINT test_reject_tool_call"
            )
        else:
            conn.execute("DROP TRIGGER test_skip_tool_call_insert ON tool_calls")
            conn.execute("DROP FUNCTION test_skip_tool_call_insert()")

    expected = {"counter_name": COUNTER_NAME, "value": 1 if initial is None else 8}
    assert increment(store, task_id, attempt_id) == expected
    committed = read_state(store)
    assert_execution_unchanged(before, committed)
    assert committed["counters"] == [expected]
    assert len(committed["calls"]) == 1
    assert_call(committed["calls"][0], task_id, expected)


@pytest.mark.parametrize("state", ["missing", "queued", "failed", "succeeded"])
def test_missing_or_non_running_task_cannot_execute_tool(store, state):
    task_id, attempt_id = uuid4(), uuid4()
    if state == "queued":
        task_id = create_task(store)["task_id"]
    elif state in ("failed", "succeeded"):
        task_id, attempt_id = make_running_task(store)
        if state == "failed":
            closed = store.try_fail_task_with_attempt(
                task_id, attempt_id, {"message": "task already failed"}
            )
        else:
            closed = store.try_succeed_task_with_attempt(
                task_id, attempt_id, {"answer": "task already finished"}
            )
        assert closed is not None and closed["status"] == state
    seed_counter(store, 7)
    before = read_state(store)

    assert increment(store, task_id, attempt_id) is None

    assert read_state(store) == before


@pytest.mark.parametrize("wrong_id", ["missing", "other-task"])
def test_wrong_attempt_cannot_execute_tool(store, wrong_id):
    task_id, _ = make_running_task(store)
    if wrong_id == "other-task":
        _, attempt_id = make_running_task(store)
    else:
        attempt_id = uuid4()
    seed_counter(store, 7)
    before = read_state(store)

    assert increment(store, task_id, attempt_id) is None

    assert read_state(store) == before


@pytest.mark.parametrize("broken", ["missing-attempt", "latest-closed"])
def test_inconsistent_running_task_raises_before_tool_side_effect(store, broken):
    if broken == "missing-attempt":
        task_id = create_task(store)["task_id"]
        attempt_id = uuid4()
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'running' WHERE task_id = %s", (task_id,)
            )
        message = "running Task 缺少 Attempt"
    else:
        task_id, attempt_id = make_running_task(store)
        # 旧 Attempt 仍 running，但最新 Attempt 已关闭；查询不能先筛 running。
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute("""
                INSERT INTO task_attempts (
                    attempt_id, task_id, attempt_no, status, finished_at
                )
                VALUES (%s, %s, 2, 'failed', CURRENT_TIMESTAMP)
            """, (uuid4(), task_id))
        message = "最新 Attempt 不是 running"
    seed_counter(store, 7)
    before = read_state(store)

    with pytest.raises(RuntimeError, match=message):
        increment(store, task_id, attempt_id)

    assert read_state(store) == before


@pytest.mark.parametrize("duplicate", ["tool-call-id", "logical-position"])
def test_database_enforces_both_tool_call_uniqueness_constraints(store, duplicate):
    task_id, attempt_id = make_running_task(store)
    increment(store, task_id, attempt_id)
    before = read_state(store)
    original = before["calls"][0]
    duplicate_id = original["tool_call_id"] if duplicate == "tool-call-id" else uuid4()
    duplicate_index = 1 if duplicate == "tool-call-id" else 0

    # 每种情况只冲突一个唯一约束，验证迁移本身提供数据库级约束。
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute("""
                INSERT INTO tool_calls (
                    tool_call_id, task_id, step_index, call_index,
                    tool_name, input_fingerprint, result
                )
                SELECT %s, task_id, step_index, %s,
                       tool_name, input_fingerprint, result
                FROM tool_calls
                WHERE tool_call_id = %s
            """, (duplicate_id, duplicate_index, original["tool_call_id"]))

    assert caught.value.diag.table_name == "tool_calls"
    assert read_state(store) == before
