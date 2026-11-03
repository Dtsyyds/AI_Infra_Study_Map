"""真实 PostgreSQL：同库工具调用账本的幂等性与事务验收。

运行位置：仓库根目录；沿用 AGENT_DATABASE_URL。
AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
    -p pytest_asyncio.plugin --tb=short -x -q \
    month04_deploy/tests/test_tool_call_idempotency_integration.py

每个用例使用独立随机 schema 并在结束后清理。默认加载 001～010，升级用例先在 008 中写入旧数据。
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
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
STATUS_MIGRATION = MIGRATIONS_DIR / "009_add_tool_call_status.sql"


@pytest.fixture
def store(request):
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"
    assert callable(getattr(TaskStore, "try_increment_counter_once", None)), (
        "实际导入的 TaskStore 缺少 try_increment_counter_once"
    )

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
    migrations = [MIGRATIONS_DIR / name for name in names]
    if getattr(request, "param", None) != "before_009":
        migrations.append(STATUS_MIGRATION)
        migrations.append(MIGRATIONS_DIR / "010_add_task_lease.sql")
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


def claim_running_task(store):
    created = create_task(store)
    return assert_claimed(store.try_start_task_with_attempt(created["task_id"]))


def make_running_task(store):
    task, attempt = claim_running_task(store)
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
    assert row["status"] == "succeeded"
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
    running_task, claimed_attempt = claim_running_task(store)
    task_id, first_attempt_id = running_task["task_id"], claimed_attempt["attempt_id"]
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
        expected_lease_version=running_task["lease_version"],
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
        running_task, claimed_attempt = claim_running_task(store)
        task_id, attempt_id = running_task["task_id"], claimed_attempt["attempt_id"]
        if state == "failed":
            closed = store.try_fail_task_with_attempt(
                task_id, attempt_id, {"message": "task already failed"},
                expected_lease_version=running_task["lease_version"],
            )
        else:
            closed = store.try_succeed_task_with_attempt(
                task_id, attempt_id, {"answer": "task already finished"},
                expected_lease_version=running_task["lease_version"],
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


@pytest.mark.parametrize("store", ["before_009"], indirect=True)
def test_009_upgrades_committed_call_and_preserves_original_result(store):
    # 在 008 的表结构中准备已提交的 Task 和 Attempt。
    task_id, attempt_id = uuid4(), uuid4()
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO tasks (task_id, task_type, payload, status)
            VALUES (%s, 'demo.echo', '{"text":"before 009"}'::jsonb, 'running')
            """,
            (task_id,),
        )
        conn.execute(
            """
            INSERT INTO task_attempts (attempt_id, task_id, attempt_no, status)
            VALUES (%s, %s, 1, 'running')
            """,
            (attempt_id, task_id),
        )
    seed_counter(store, 7)
    expected = {"counter_name": COUNTER_NAME, "value": 8}
    assert increment(store, task_id, attempt_id) == expected

    before = read_state(store)
    assert len(before["calls"]) == 1
    old_call = before["calls"][0]
    assert "status" not in old_call
    assert old_call["result"] == expected
    assert old_call["completed_at"] is not None

    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(STATUS_MIGRATION.read_text(encoding="utf-8"), prepare=False)

    upgraded = read_state(store)
    assert upgraded == {
        **before,
        "calls": [{**old_call, "status": "succeeded"}],
    }
    assert increment(store, task_id, attempt_id) == expected
    assert read_state(store) == upgraded


def insert_status_fixture_row(store, task_id, status, result_json, has_completed_at):
    """直接插入测试行，让 PostgreSQL 校验状态与两个可空字段的组合。"""
    call_id = uuid4()
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO tool_calls (
                tool_call_id, task_id, step_index, call_index, tool_name,
                input_fingerprint, status, result, completed_at
            )
            VALUES (
                %s, %s, 0, 0, 'demo.external_v1', 'fixture-fingerprint',
                %s, %s::jsonb, CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            """,
            (call_id, task_id, status, result_json, has_completed_at),
        )
    return call_id


@pytest.mark.parametrize("status", ["started", "unknown"])
def test_009_stores_unresolved_call_without_result_or_completion_time(store, status):
    task_id, _ = make_running_task(store)
    call_id = insert_status_fixture_row(store, task_id, status, None, False)

    calls = read_state(store)["calls"]
    assert len(calls) == 1
    row = calls[0]
    assert row["tool_call_id"] == call_id
    assert row["status"] == status
    assert row["result"] is None
    assert row["completed_at"] is None


@pytest.mark.parametrize(
    ("status", "result_json", "has_completed_at"),
    [
        pytest.param("succeeded", None, True, id="success-missing-result"),
        pytest.param("succeeded", "{}", False, id="success-missing-time"),
        pytest.param("started", "{}", False, id="started-with-result"),
        pytest.param("started", None, True, id="started-with-time"),
        pytest.param("unknown", "{}", False, id="unknown-with-result"),
        pytest.param("unknown", None, True, id="unknown-with-time"),
        pytest.param("unlisted-status", None, False, id="unlisted-status"),
    ],
)
def test_009_refuses_inconsistent_call(store, status, result_json, has_completed_at):
    task_id, _ = make_running_task(store)
    before = read_state(store)

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        insert_status_fixture_row(store, task_id, status, result_json, has_completed_at)

    assert caught.value.diag.constraint_name == "tool_calls_status_result_check"
    assert read_state(store) == before


def prepare_external(
    store, task_id, attempt_id, *,
    tool_name="demo.external_ticket_v1", arguments=None, call_index=0,
):
    if arguments is None:
        arguments = {"queue": "infra", "priority": 2}
    return store.try_prepare_external_tool_call(
        task_id,
        attempt_id,
        step_index=0,
        call_index=call_index,
        tool_name=tool_name,
        arguments=arguments,
    )


def test_prepare_external_commits_started_and_only_first_caller_may_send(store):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)

    row, inserted = prepare_external(store, task_id, attempt_id)
    assert inserted is True
    assert isinstance(row["tool_call_id"], UUID)
    assert (row["task_id"], row["step_index"], row["call_index"]) == (task_id, 0, 0)
    assert row["tool_name"] == "demo.external_ticket_v1"
    assert row["input_fingerprint"]
    assert (row["status"], row["result"], row["completed_at"]) == ("started", None, None)
    committed = read_state(store)
    assert committed == {**before, "calls": [row]}

    # 相同参数使用另一种键顺序，另一实例应只读取已提交的意图。
    duplicate = prepare_external(
        TaskStore(store.dsn), task_id, attempt_id,
        arguments={"priority": 2, "queue": "infra"},
    )
    assert duplicate == (row, False)
    assert read_state(store) == committed


def test_prepare_external_handles_same_position_with_another_stored_uuid(store):
    task_id, attempt_id = make_running_task(store)
    original, inserted = prepare_external(store, task_id, attempt_id)
    assert inserted is True
    other_id = uuid4()
    # 构造同一逻辑位置已有不同 UUID 的旧记录，专门验证 ON CONFLICT 的目标。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "UPDATE tool_calls SET tool_call_id = %s WHERE tool_call_id = %s",
            (other_id, original["tool_call_id"]),
        )
    before = read_state(store)
    assert before["calls"] == [{**original, "tool_call_id": other_id}]

    assert prepare_external(store, task_id, attempt_id) == (before["calls"][0], False)
    assert read_state(store) == before


@pytest.mark.parametrize("status", ["succeeded", "unknown"])
def test_prepare_external_preserves_existing_outcome(store, status):
    task_id, attempt_id = make_running_task(store)
    first, inserted = prepare_external(store, task_id, attempt_id)
    assert inserted is True
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        if status == "succeeded":
            conn.execute("""
                UPDATE tool_calls
                SET status = 'succeeded', result = '{"receipt": "ticket-7"}'::jsonb,
                    completed_at = CURRENT_TIMESTAMP
                WHERE tool_call_id = %s AND status = 'started'
            """, (first["tool_call_id"],))
        else:
            conn.execute("""
                UPDATE tool_calls SET status = 'unknown'
                WHERE tool_call_id = %s AND status = 'started'
            """, (first["tool_call_id"],))
    before = read_state(store)
    assert before["calls"][0]["status"] == status

    assert prepare_external(store, task_id, attempt_id) == (before["calls"][0], False)
    assert read_state(store) == before


@pytest.mark.parametrize("change", ["arguments", "tool-name"])
def test_prepare_external_rejects_changed_request_without_overwriting_started(store, change):
    task_id, attempt_id = make_running_task(store)
    row, inserted = prepare_external(store, task_id, attempt_id)
    assert inserted is True and row["status"] == "started"
    before = read_state(store)
    overrides = (
        {"arguments": {"queue": "another-queue", "priority": 2}}
        if change == "arguments" else {"tool_name": "demo.other_v1"}
    )

    with pytest.raises(ValueError, match="同一工具调用的名称或参数发生变化"):
        prepare_external(store, task_id, attempt_id, **overrides)
    assert read_state(store) == before


def test_prepare_external_new_attempt_can_read_confirmed_result_and_old_attempt_is_refused(store):
    running_task, claimed_attempt = claim_running_task(store)
    task_id, old_id = running_task["task_id"], claimed_attempt["attempt_id"]
    row, inserted = prepare_external(store, task_id, old_id)
    assert inserted is True
    # 准备已确认成功的工具结果。之后失败的是同一 Task 的后续步骤。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            UPDATE tool_calls
            SET status = 'succeeded', result = '{"receipt": "ticket-8"}'::jsonb,
                completed_at = CURRENT_TIMESTAMP
            WHERE tool_call_id = %s AND status = 'started'
        """, (row["tool_call_id"],))
    confirmed = read_state(store)["calls"][0]
    assert confirmed["status"] == "succeeded"
    assert confirmed["result"] == {"receipt": "ticket-8"}

    failed = store.try_fail_task_with_attempt(
        task_id, old_id, {"message": "failure in a later step"},
        expected_lease_version=running_task["lease_version"],
    )
    assert failed is not None and failed["status"] == "failed"
    after_failure = read_state(store)
    assert prepare_external(store, task_id, old_id) is None
    assert read_state(store) == after_failure

    policy = dict(retryable_error=True, outcome_unknown=False, replay_safe=True)
    scheduled = store.try_schedule_task_retry(task_id, old_id, delay_seconds=0, **policy)
    assert isinstance(scheduled, dict) and scheduled["next_retry_at"] is not None
    _, new_attempt = assert_claimed(store.try_retry_task_with_attempt(task_id, old_id, **policy))
    assert new_attempt["attempt_no"] == 2 and new_attempt["attempt_id"] != old_id
    before = read_state(store)

    assert prepare_external(store, task_id, old_id) is None
    assert prepare_external(store, task_id, old_id, call_index=1) is None
    assert prepare_external(store, task_id, new_attempt["attempt_id"]) == (confirmed, False)
    assert read_state(store) == before


def test_prepare_external_concurrent_callers_receive_only_one_grant(store):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)
    barrier = Barrier(2)

    def contender():
        independent = TaskStore(store.dsn)
        barrier.wait(timeout=10)
        return prepare_external(independent, task_id, attempt_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    (row_a, flag_a), (row_b, flag_b) = results
    assert sorted([flag_a, flag_b]) == [False, True]
    assert row_a == row_b and row_a["status"] == "started"
    assert read_state(store) == {**before, "calls": [row_a]}


@pytest.mark.parametrize("fault", ["empty-returning", "commit-error"])
def test_prepare_external_insert_failure_cannot_return_a_send_grant(store, fault):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        if fault == "empty-returning":
            conn.execute("""
                CREATE FUNCTION test_skip_external_prepare() RETURNS trigger
                LANGUAGE plpgsql AS $body$ BEGIN RETURN NULL; END; $body$
            """)
            conn.execute("""
                CREATE TRIGGER test_skip_external_prepare BEFORE INSERT ON tool_calls
                FOR EACH ROW EXECUTE FUNCTION test_skip_external_prepare()
            """)
        else:
            conn.execute("""
                CREATE FUNCTION test_fail_external_prepare_commit() RETURNS trigger
                LANGUAGE plpgsql AS $body$
                BEGIN
                    RAISE EXCEPTION 'injected external prepare commit error';
                    RETURN NEW;
                END;
                $body$
            """)
            conn.execute("""
                CREATE CONSTRAINT TRIGGER test_fail_external_prepare_commit
                AFTER INSERT ON tool_calls DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION test_fail_external_prepare_commit()
            """)

    if fault == "empty-returning":
        with pytest.raises(RuntimeError, match="工具调用冲突后未找到账本"):
            prepare_external(store, task_id, attempt_id)
    else:
        with pytest.raises(psycopg.errors.RaiseException, match="injected external prepare commit error"):
            prepare_external(store, task_id, attempt_id)

    assert read_state(store) == before


def make_started_external_call(store):
    task_id, attempt_id = make_running_task(store)
    row, inserted = prepare_external(store, task_id, attempt_id)
    assert inserted is True and row["status"] == "started"
    return task_id, row


def write_ticket_outcome(store, task_id, call_id, receipt):
    if receipt is None:
        return store.try_mark_external_tool_call_unknown(task_id, call_id)
    return store.try_succeed_external_tool_call(task_id, call_id, result=receipt)


def test_external_writeback_first_receipt_survives_repeat_and_late_unknown(store):
    task_id, started = make_started_external_call(store)
    call_id = started["tool_call_id"]
    before = read_state(store)
    receipt = {"ticket_id": "T-7"}

    row = store.try_succeed_external_tool_call(task_id, call_id, result=receipt)
    assert row["completed_at"] is not None
    assert row == {
        **started, "status": "succeeded", "result": receipt,
        "completed_at": row["completed_at"],
    }
    committed = read_state(store)
    assert committed == {**before, "calls": [row]}

    # 已提交，调用方在另一连接重试本地写回。较晚的写入不能改变首份回执。
    reopened = TaskStore(store.dsn)
    assert reopened.try_succeed_external_tool_call(task_id, call_id, result=receipt) == row
    assert reopened.try_succeed_external_tool_call(
        task_id, call_id, result={"ticket_id": "late-receipt"}
    ) == row
    assert reopened.try_mark_external_tool_call_unknown(task_id, call_id) == row
    assert read_state(store) == committed


def test_external_writeback_unknown_is_preserved_for_reconciliation(store):
    task_id, started = make_started_external_call(store)
    call_id = started["tool_call_id"]
    before = read_state(store)

    row = store.try_mark_external_tool_call_unknown(task_id, call_id)
    assert row == {**started, "status": "unknown"}
    committed = read_state(store)
    assert committed == {**before, "calls": [row]}

    assert store.try_mark_external_tool_call_unknown(task_id, call_id) == row
    assert store.try_succeed_external_tool_call(
        task_id, call_id, result={"ticket_id": "T-7"}
    ) == row
    assert read_state(store) == committed


@pytest.mark.parametrize("bad_key", ["task-id", "tool-call-id"])
@pytest.mark.parametrize("receipt", [{"ticket_id": "T-7"}, None], ids=["success", "unknown"])
def test_external_writeback_missing_registered_pair_raises(store, bad_key, receipt):
    task_id, started = make_started_external_call(store)
    call_id = started["tool_call_id"]
    if bad_key == "task-id":
        task_id = create_task(store)["task_id"]
    else:
        call_id = uuid4()
    before = read_state(store)

    with pytest.raises(RuntimeError, match="已登记的工具调用缺少账本"):
        write_ticket_outcome(store, task_id, call_id, receipt)
    assert read_state(store) == before


def test_external_writeback_concurrent_outcomes_return_the_committed_winner(store):
    task_id, started = make_started_external_call(store)
    before = read_state(store)
    barrier = Barrier(2)
    receipt = {"ticket_id": "T-7"}

    def contender(value):
        independent = TaskStore(store.dsn)
        barrier.wait(timeout=10)
        return write_ticket_outcome(independent, task_id, started["tool_call_id"], value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        success_future = pool.submit(contender, receipt)
        unknown_future = pool.submit(contender, None)
        success_view = success_future.result(timeout=20)
        unknown_view = unknown_future.result(timeout=20)

    after = read_state(store)
    assert len(after["calls"]) == 1
    winner = after["calls"][0]
    assert success_view == unknown_view == winner
    assert winner["status"] in ("succeeded", "unknown")
    if winner["status"] == "succeeded":
        assert winner["result"] == receipt
        assert winner["completed_at"] is not None
    else:
        assert winner["result"] is None and winner["completed_at"] is None
    assert after == {**before, "calls": [winner]}


@pytest.mark.parametrize("receipt", [{"ticket_id": "T-7"}, None], ids=["success", "unknown"])
def test_external_writeback_commit_error_rolls_back_then_local_update_can_retry(store, receipt):
    task_id, started = make_started_external_call(store)
    call_id = started["tool_call_id"]
    before = read_state(store)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_reject_external_writeback_commit() RETURNS trigger
            LANGUAGE plpgsql AS $body$
            BEGIN
                RAISE EXCEPTION 'injected external writeback commit error';
                RETURN NEW;
            END;
            $body$
        """)
        conn.execute("""
            CREATE CONSTRAINT TRIGGER test_reject_external_writeback_commit
            AFTER UPDATE ON tool_calls DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION test_reject_external_writeback_commit()
        """)

    with pytest.raises(psycopg.errors.RaiseException, match="injected external writeback commit error"):
        write_ticket_outcome(store, task_id, call_id, receipt)
    assert read_state(store) == before

    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("DROP TRIGGER test_reject_external_writeback_commit ON tool_calls")
        conn.execute("DROP FUNCTION test_reject_external_writeback_commit()")

    row = write_ticket_outcome(TaskStore(store.dsn), task_id, call_id, receipt)
    if receipt is None:
        assert row == {**started, "status": "unknown"}
    else:
        assert row["completed_at"] is not None
        assert row == {
            **started, "status": "succeeded", "result": receipt,
            "completed_at": row["completed_at"],
        }
    assert read_state(store) == {**before, "calls": [row]}


def test_external_writeback_reports_actual_started_row_if_update_is_skipped(store):
    task_id, started = make_started_external_call(store)
    call_id = started["tool_call_id"]
    before = read_state(store)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION test_skip_external_writeback() RETURNS trigger
            LANGUAGE plpgsql AS $body$ BEGIN RETURN NULL; END; $body$
        """)
        conn.execute("""
            CREATE TRIGGER test_skip_external_writeback BEFORE UPDATE ON tool_calls
            FOR EACH ROW EXECUTE FUNCTION test_skip_external_writeback()
        """)

    assert store.try_succeed_external_tool_call(task_id, call_id, result={"ticket_id": "T-7"}) == started
    assert store.try_mark_external_tool_call_unknown(task_id, call_id) == started
    assert read_state(store) == before
