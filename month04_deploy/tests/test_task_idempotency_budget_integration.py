"""真实 PostgreSQL：幂等创建时固定重试预算（31 个用例）。

放置位置：month04_deploy/tests/test_task_idempotency_budget_integration.py
接口：TaskStore.create_task_idempotent(..., retry_deadline_at=None, max_attempts=1)。

同 key、相同内容和预算复用；任一内容或预算不同则报幂等冲突。
省略预算参数使用默认值，不表示接受原任务的任意预算。

运行：
    AGENT_RUN_DB_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest -p pytest_asyncio.plugin --tb=short -x -q \
        month04_deploy/tests/test_task_idempotency_budget_integration.py

每个用例建立独立随机 schema，执行 001～006，最后仅删除自己的 schema。
通过新连接核对提交后的 Task、Attempt 和步骤；不 mock SQL、连接或 fetchone。
不会给 AGENT_DATABASE_URL 原本指向的表补迁移。
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable.idempotency import IdempotencyConflictError
from month04_deploy.app.durable.task_store import TaskStore


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)

DEADLINE = datetime(2035, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)


@pytest.fixture
def store():
    base_dsn = os.getenv("AGENT_DATABASE_URL")
    assert base_dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = [
        migrations_dir / "001_create_tasks.sql",
        migrations_dir / "002_create_task_steps.sql",
        migrations_dir / "003_add_task_idempotency.sql",
        migrations_dir / "004_create_task_attempts.sql",
        migrations_dir / "005_add_task_retry_deadline.sql",
        migrations_dir / "006_add_task_max_attempts.sql",
    ]
    for migration in migrations:
        assert migration.is_file(), f"缺少迁移文件：{migration}"

    schema_name = f"idem_budget_test_{uuid4().hex}"
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
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))

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


def read_snapshot(store):
    """调用完成后用新连接回读完整记录，包括时间、结果和错误。"""
    with psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as conn:
        return {
            "tasks": conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall(),
            "attempts": conn.execute(
                "SELECT * FROM task_attempts ORDER BY task_id, attempt_no"
            ).fetchall(),
            "steps": conn.execute(
                "SELECT * FROM task_steps ORDER BY task_id, step_index"
            ).fetchall(),
        }


def create_request(store, key, **overrides):
    """普通用例显式指定非默认预算；测试默认值时直接调用生产接口。"""
    request = {
        "task_type": "demo.echo",
        "payload": {"text": "fixed idempotent request"},
        "idempotency_key": key,
        "retry_deadline_at": DEADLINE,
        "max_attempts": 3,
    }
    request.update(overrides)
    return store.create_task_idempotent(**request)


@pytest.mark.parametrize("explicit_defaults", [False, True], ids=["omitted", "explicit"])
def test_default_budget_is_persisted_and_reusable(store, explicit_defaults):
    key = uuid4().hex
    payload = {"text": "default budget"}
    explicit = {"retry_deadline_at": None, "max_attempts": 1}
    first_kwargs = explicit if explicit_defaults else {}
    repeat_kwargs = {} if explicit_defaults else explicit

    created = store.create_task_idempotent("demo.echo", payload, key, **first_kwargs)

    assert created["status"] == "queued"
    assert created["retry_deadline_at"] is None
    assert created["max_attempts"] == 1
    assert created["payload"] == payload
    assert created["idempotency_key"] == key
    before = read_snapshot(store)
    assert before == {"tasks": [created], "attempts": [], "steps": []}

    reused = store.create_task_idempotent("demo.echo", payload, key, **repeat_kwargs)

    assert reused == created
    assert read_snapshot(store) == before


def test_nondefault_budget_is_committed_with_new_task(store):
    key = uuid4().hex

    created = create_request(store, key)

    assert created["status"] == "queued"
    assert created["retry_deadline_at"] == DEADLINE
    assert created["max_attempts"] == 3
    assert created["idempotency_key"] == key
    assert read_snapshot(store) == {"tasks": [created], "attempts": [], "steps": []}


@pytest.mark.parametrize("status", ["queued", "running", "failed", "succeeded"])
def test_same_request_preserves_existing_execution_state(store, status):
    key = uuid4().hex
    created = create_request(store, key)
    task_id = created["task_id"]

    if status != "queued":
        claimed = store.try_start_task_with_attempt(task_id)
        assert claimed is not None
        _, attempt = claimed
        store.save_completed_step(task_id, 0, "read_input_v1", created["payload"])
        if status == "failed":
            closed = store.try_fail_task_with_attempt(
                task_id, attempt["attempt_id"], {"type": "ValueError", "message": "saved failure"}
            )
            assert closed is not None
        elif status == "succeeded":
            closed = store.try_succeed_task_with_attempt(
                task_id, attempt["attempt_id"], {"answer": "saved result"}
            )
            assert closed is not None

    before = read_snapshot(store)
    assert before["tasks"][0]["status"] == status

    reused = create_request(store, key)

    assert reused == before["tasks"][0]
    # 不能刷新 updated_at、清空结果/错误、重开任务、增加 Attempt 或改写步骤。
    assert read_snapshot(store) == before


@pytest.mark.parametrize("new_limit", [1, 10], ids=["decrease", "increase"])
def test_changed_attempt_limit_conflicts_without_modification(store, new_limit):
    key = uuid4().hex
    create_request(store, key)
    before = read_snapshot(store)

    with pytest.raises(IdempotencyConflictError):
        create_request(store, key, max_attempts=new_limit)

    assert read_snapshot(store) == before


@pytest.mark.parametrize(
    "stored_deadline,new_deadline",
    [
        (DEADLINE, DEADLINE + timedelta(hours=1)),
        (DEADLINE, DEADLINE - timedelta(hours=1)),
        (DEADLINE, None),
        (None, DEADLINE),
    ],
    ids=["extend", "shorten", "remove", "add"],
)
def test_changed_deadline_conflicts_without_modification(store, stored_deadline, new_deadline):
    key = uuid4().hex
    create_request(store, key, retry_deadline_at=stored_deadline)
    before = read_snapshot(store)

    with pytest.raises(IdempotencyConflictError):
        create_request(store, key, retry_deadline_at=new_deadline)

    assert read_snapshot(store) == before


def test_omitted_limit_means_one_and_conflicts_with_existing_three(store):
    key = uuid4().hex
    created = create_request(store, key)
    before = read_snapshot(store)

    with pytest.raises(IdempotencyConflictError):
        store.create_task_idempotent(
            "demo.echo", created["payload"], key, retry_deadline_at=DEADLINE
        )

    assert read_snapshot(store) == before


@pytest.mark.parametrize("offset_hours", [8, -7], ids=["utc-plus-eight", "utc-minus-seven"])
def test_same_instant_in_another_timezone_reuses_task(store, offset_hours):
    key = uuid4().hex
    created = create_request(store, key)
    before = read_snapshot(store)
    same_instant = DEADLINE.astimezone(timezone(timedelta(hours=offset_hours)))
    assert same_instant.isoformat() != DEADLINE.isoformat()

    reused = create_request(store, key, retry_deadline_at=same_instant)

    assert reused == created
    assert read_snapshot(store) == before


def test_same_wall_clock_with_different_actual_instant_conflicts(store):
    key = uuid4().hex
    create_request(store, key)
    before = read_snapshot(store)
    # replace 更换时区标记而不调整钟面时间，因此这里表示另一个真实时刻。
    different_instant = DEADLINE.replace(tzinfo=timezone(timedelta(hours=8)))
    assert different_instant != DEADLINE

    with pytest.raises(IdempotencyConflictError):
        create_request(store, key, retry_deadline_at=different_instant)

    assert read_snapshot(store) == before


@pytest.mark.parametrize(
    "changed_content",
    [{"task_type": "demo.other"}, {"payload": {"text": "different request"}}],
    ids=["task-type", "payload"],
)
def test_same_budget_does_not_hide_changed_request_content(store, changed_content):
    key = uuid4().hex
    create_request(store, key)
    before = read_snapshot(store)

    with pytest.raises(IdempotencyConflictError):
        create_request(store, key, **changed_content)

    assert read_snapshot(store) == before


class ConnectionCountingStore(TaskStore):
    """仅观察连接次数；如果被调用，仍建立真实 PostgreSQL 连接。"""

    def __init__(self, dsn):
        super().__init__(dsn)
        self.connect_calls = 0

    def _connect(self):
        self.connect_calls += 1
        return super()._connect()


@pytest.mark.parametrize(
    "invalid_limit,expected_error",
    [
        (None, TypeError),
        (True, TypeError),
        (False, TypeError),
        ("3", TypeError),
        (3.0, TypeError),
        (0, ValueError),
        (-1, ValueError),
    ],
    ids=["none", "true", "false", "string", "float", "zero", "negative"],
)
def test_invalid_limit_is_rejected_before_connection_even_for_existing_key(
    store, invalid_limit, expected_error
):
    key = uuid4().hex
    create_request(store, key)
    before = read_snapshot(store)
    checked_store = ConnectionCountingStore(store.dsn)

    with pytest.raises(expected_error):
        create_request(checked_store, key, max_attempts=invalid_limit)

    assert checked_store.connect_calls == 0
    assert read_snapshot(store) == before


class NoOffsetTimezone(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize(
    "invalid_deadline",
    [DEADLINE.replace(tzinfo=None), DEADLINE.replace(tzinfo=NoOffsetTimezone())],
    ids=["no-tzinfo", "no-utc-offset"],
)
def test_invalid_timezone_is_rejected_before_connection_even_for_existing_key(
    store, invalid_deadline
):
    key = uuid4().hex
    create_request(store, key)
    before = read_snapshot(store)
    checked_store = ConnectionCountingStore(store.dsn)

    with pytest.raises(ValueError, match="时区"):
        create_request(checked_store, key, retry_deadline_at=invalid_deadline)

    assert checked_store.connect_calls == 0
    assert read_snapshot(store) == before


@pytest.mark.parametrize(
    "second_budget",
    [
        {"retry_deadline_at": DEADLINE, "max_attempts": 3},
        {"retry_deadline_at": DEADLINE, "max_attempts": 10},
        {"retry_deadline_at": DEADLINE + timedelta(hours=1), "max_attempts": 3},
    ],
    ids=["same-budget", "different-limit", "different-deadline"],
)
def test_concurrent_same_key_creates_one_task_and_preserves_winning_budget(store, second_budget):
    key = uuid4().hex
    first_budget = {"retry_deadline_at": DEADLINE, "max_attempts": 3}
    gate = Barrier(2)

    def submit(budget):
        # 两个独立 TaskStore，各自创建自己的真实连接和事务。
        contender = TaskStore(store.dsn)
        gate.wait(timeout=5)
        try:
            row = create_request(contender, key, **budget)
        except IdempotencyConflictError:
            return "conflict", None, budget
        return "ok", row, budget

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, budget) for budget in (first_budget, second_budget)]
        outcomes = [future.result(timeout=20) for future in futures]

    snapshot = read_snapshot(store)
    assert len(snapshot["tasks"]) == 1
    assert snapshot["attempts"] == []
    assert snapshot["steps"] == []
    saved = snapshot["tasks"][0]
    assert saved["status"] == "queued"
    assert saved["idempotency_key"] == key

    accepted = [(row, budget) for result, row, budget in outcomes if result == "ok"]
    rejected = [result for result, _, _ in outcomes if result == "conflict"]
    if first_budget == second_budget:
        assert len(accepted) == 2
        assert rejected == []
    else:
        assert len(accepted) == 1
        assert len(rejected) == 1

    for row, budget in accepted:
        assert row == saved
        assert saved["retry_deadline_at"] == budget["retry_deadline_at"]
        assert saved["max_attempts"] == budget["max_attempts"]
