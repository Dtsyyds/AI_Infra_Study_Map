"""Worker 外部工具验收。假工单服务用独立连接提交每次请求，新连接可回读工单数。"""

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from month04_deploy.app.durable.retry_policy import RetryDecision
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.app.durable.worker import (
    ExternalToolReceiptPending,
    reconcile_external_tool_call_after_sender_exit,
    run_external_tool_once,
)
from month04_deploy.tests.test_tool_call_idempotency_integration import (
    make_running_task,
    read_state,
    store as store,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


class FakeTicketService:
    """同一个调用 ID 每到达一次都会产生一张工单。"""

    def __init__(self, dsn):
        self.dsn = dsn
        self.lose_reply = False

    def send(self, tool_call_id, arguments):
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            number = conn.execute(
                """
                INSERT INTO test_fake_tickets (tool_call_id, arguments)
                VALUES (%s, %s) RETURNING ticket_no
                """,
                (tool_call_id, Jsonb(arguments)),
            ).fetchone()[0]
        if self.lose_reply:
            raise TimeoutError("等待工单回执超时")
        return {"ticket_id": f"T-{number}"}

    def tickets(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            return conn.execute(
                "SELECT * FROM test_fake_tickets ORDER BY ticket_no"
            ).fetchall()


@pytest.fixture
def fake(store):
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE TABLE test_fake_tickets (
                ticket_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tool_call_id UUID NOT NULL,
                arguments JSONB NOT NULL
            )
        """)
    return FakeTicketService(store.dsn)


def run_ticket(store, task_id, attempt_id, send, *, arguments=None, call_index=0):
    if arguments is None:
        arguments = {"queue": "infra", "priority": 2}
    return run_external_tool_once(
        store, task_id, attempt_id,
        step_index=0, call_index=call_index,
        tool_name="demo.external_ticket_v1", arguments=arguments, send=send,
    )


def test_worker_external_timeout_after_ticket_creation_reconciles_twice_with_one_send(store, fake):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)
    fake.lose_reply = True

    assert run_ticket(store, task_id, attempt_id, fake.send) is RetryDecision.RECONCILE

    committed = read_state(store)
    assert len(committed["calls"]) == 1
    row = committed["calls"][0]
    assert (row["status"], row["result"], row["completed_at"]) == ("unknown", None, None)
    assert committed == {**before, "calls": [row]}
    tickets = fake.tickets()
    assert tickets == [{
        "ticket_no": 1, "tool_call_id": row["tool_call_id"],
        "arguments": {"queue": "infra", "priority": 2},
    }]

    # 重新打开 Store，同一个服务仍然持有第一次创建的工单。
    assert run_ticket(TaskStore(store.dsn), task_id, attempt_id, fake.send) is RetryDecision.RECONCILE
    assert fake.tickets() == tickets
    assert read_state(store) == committed


def test_worker_external_success_replays_saved_receipt_after_another_real_send(store, fake):
    task_id, attempt_id = make_running_task(store)
    one, two = {"ticket_id": "T-1"}, {"ticket_id": "T-2"}
    assert run_ticket(store, task_id, attempt_id, fake.send) == one
    assert run_ticket(store, task_id, attempt_id, fake.send, call_index=1) == two
    committed = read_state(store)
    tickets = fake.tickets()
    assert [row["result"] for row in committed["calls"]] == [one, two]
    assert all(row["status"] == "succeeded" for row in committed["calls"])
    assert [t["tool_call_id"] for t in tickets] == [
        row["tool_call_id"] for row in committed["calls"]
    ]

    assert run_ticket(TaskStore(store.dsn), task_id, attempt_id, fake.send) == one
    assert fake.tickets() == tickets
    assert read_state(store) == committed


def test_worker_external_active_sender_keeps_grant_and_releases_task_lock_before_send(store, fake):
    task_id, attempt_id = make_running_task(store)
    started_send, finish_send = Event(), Event()
    original = {"queue": "infra", "options": {"priority": 2}}

    def blocking_send(call_id, arguments):
        # 独立连接必须能读到已提交的 started，也能立即取得 Task 行锁。
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            assert conn.execute(
                "SELECT status FROM tool_calls WHERE tool_call_id = %s", (call_id,)
            ).fetchone() == ("started",)
            assert conn.execute(
                "SELECT task_id FROM tasks WHERE task_id = %s FOR UPDATE NOWAIT",
                (task_id,),
            ).fetchone() == (task_id,)
        started_send.set()
        assert finish_send.wait(timeout=12), "未收到释放假发送方的信号"
        return fake.send(call_id, arguments)

    with ThreadPoolExecutor(max_workers=1) as pool:
        sender = pool.submit(run_ticket, store, task_id, attempt_id, blocking_send, arguments=original)
        try:
            assert started_send.wait(timeout=10), "发送方未进入请求阶段"
            before_duplicate = read_state(store)
            assert before_duplicate["calls"][0]["status"] == "started"
            original["options"]["priority"] = 99
            assert run_ticket(
                TaskStore(store.dsn), task_id, attempt_id, fake.send,
                arguments={"queue": "infra", "options": {"priority": 2}},
            ) is None
            assert read_state(store) == before_duplicate
            assert fake.tickets() == []
        finally:
            finish_send.set()
        assert sender.result(timeout=20) == {"ticket_id": "T-1"}

    tickets = fake.tickets()
    assert len(tickets) == 1
    assert tickets[0]["arguments"] == {"queue": "infra", "options": {"priority": 2}}
    assert read_state(store)["calls"][0]["status"] == "succeeded"


def test_worker_external_invalid_attempt_gets_no_send(store, fake):
    task_id, _ = make_running_task(store)
    before = read_state(store)
    assert run_ticket(store, task_id, uuid4(), fake.send) is None
    assert fake.tickets() == []
    assert read_state(store) == before


def test_worker_external_invalid_sender_fails_before_registering_intent(store, fake):
    task_id, attempt_id = make_running_task(store)
    before = read_state(store)
    with pytest.raises(TypeError, match="send 必须是可调用对象"):
        run_ticket(store, task_id, attempt_id, None)
    assert fake.tickets() == []
    assert read_state(store) == before


@pytest.mark.parametrize("winner", ["succeeded", "unknown"])
def test_worker_external_uses_actual_writeback_row_after_race(store, fake, winner):
    task_id, attempt_id = make_running_task(store)
    other_store = TaskStore(store.dsn)

    def send(call_id, arguments):
        receipt = fake.send(call_id, arguments)
        # 插入另一路已提交的结果写回，验证 Worker 使用返回行的实际状态。
        if winner == "succeeded":
            other_store.try_succeed_external_tool_call(task_id, call_id, result=receipt)
            raise TimeoutError("注入成功回执已落库后的超时")
        other_store.try_mark_external_tool_call_unknown(task_id, call_id)
        return receipt

    result = run_ticket(store, task_id, attempt_id, send)
    expected = {"ticket_id": "T-1"} if winner == "succeeded" else RetryDecision.RECONCILE
    assert result == expected
    committed = read_state(store)
    assert committed["calls"][0]["status"] == winner
    assert run_ticket(other_store, task_id, attempt_id, fake.send) == expected
    assert read_state(store) == committed
    assert len(fake.tickets()) == 1


@pytest.mark.parametrize("fault", ["before-write", "after-commit", "suppressed-update"])
def test_worker_external_known_receipt_retries_only_local_write(store, fake, monkeypatch, fault):
    task_id, attempt_id = make_running_task(store)
    succeed = store.try_succeed_external_tool_call
    if fault == "suppressed-update":
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute("""
                CREATE FUNCTION test_skip_worker_receipt() RETURNS trigger
                LANGUAGE plpgsql AS $body$ BEGIN RETURN NULL; END; $body$
            """)
            conn.execute("""
                CREATE TRIGGER test_skip_worker_receipt BEFORE UPDATE ON tool_calls
                FOR EACH ROW EXECUTE FUNCTION test_skip_worker_receipt()
            """)
    else:
        def lose_local_ack(task_id, call_id, *, result):
            if fault == "after-commit":
                succeed(task_id, call_id, result=result)
            raise TimeoutError("注入本地写回确认丢失")

        monkeypatch.setattr(store, "try_succeed_external_tool_call", lose_local_ack)

    with pytest.raises(ExternalToolReceiptPending) as caught:
        run_ticket(store, task_id, attempt_id, fake.send)
    pending = caught.value
    row = read_state(store)["calls"][0]
    assert pending.task_id == task_id and pending.tool_call_id == row["tool_call_id"]
    assert pending.receipt == {"ticket_id": "T-1"}
    assert row["status"] == ("succeeded" if fault == "after-commit" else "started")
    tickets = fake.tickets()
    assert len(tickets) == 1

    if fault == "suppressed-update":
        with psycopg.connect(store.dsn, connect_timeout=5) as conn:
            conn.execute("DROP TRIGGER test_skip_worker_receipt ON tool_calls")
    else:
        assert isinstance(pending.__cause__, TimeoutError)

    reopened = TaskStore(store.dsn)
    saved = reopened.try_succeed_external_tool_call(
        pending.task_id, pending.tool_call_id, result=pending.receipt,
    )
    assert saved["status"] == "succeeded" and saved["result"] == pending.receipt
    if fault == "after-commit":
        assert saved == row
    assert run_ticket(reopened, task_id, attempt_id, fake.send) == pending.receipt
    assert fake.tickets() == tickets


@pytest.mark.parametrize(
    ("exit_point", "expected_count", "exit_status"),
    [
        ("before-service", 0, "started"),
        ("after-service", 1, "started"),
        ("after-writeback", 1, "succeeded"),
    ],
)
def test_worker_external_recovery_after_confirmed_sender_exit(
    store, fake, exit_point, expected_count, exit_status,
):
    task_id, attempt_id = make_running_task(store)
    initial = read_state(store)
    child_code = textwrap.dedent("""
        import os
        import sys
        from uuid import UUID
        from month04_deploy.app.durable.task_store import TaskStore
        from month04_deploy.tests.test_worker_external_tool_integration import FakeTicketService, run_ticket

        store = TaskStore(os.environ["AGENT_EXTERNAL_WORKER_TEST_DSN"])
        service = FakeTicketService(store.dsn)
        exit_point = sys.argv[3]

        def send(call_id, arguments):
            if exit_point == "before-service":
                os._exit(86)
            receipt = service.send(call_id, arguments)
            if exit_point == "after-service":
                os._exit(86)
            return receipt

        result = run_ticket(store, UUID(sys.argv[1]), UUID(sys.argv[2]), send)
        assert exit_point == "after-writeback" and result == {"ticket_id": "T-1"}, result
        os._exit(86)
    """)
    child = subprocess.run(
        [sys.executable, "-c", child_code, str(task_id), str(attempt_id), exit_point],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "AGENT_EXTERNAL_WORKER_TEST_DSN": store.dsn},
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert child.returncode == 86, f"code={child.returncode}\n{child.stdout}\n{child.stderr}"

    after_exit = read_state(store)
    assert len(after_exit["calls"]) == 1 and after_exit["steps"] == []
    row = after_exit["calls"][0]
    assert row["status"] == exit_status
    assert after_exit == {**initial, "calls": [row]}
    tickets = fake.tickets()
    assert len(tickets) == expected_count
    assert all(t["tool_call_id"] == row["tool_call_id"] for t in tickets)

    reopened = TaskStore(store.dsn)
    ordinary = run_ticket(reopened, task_id, attempt_id, fake.send)
    assert ordinary == ({"ticket_id": "T-1"} if exit_status == "succeeded" else None)
    assert read_state(store) == after_exit

    # 这里已等待原发送进程退出，并确认它取得的是这一条调用的发送资格。
    outcome = reconcile_external_tool_call_after_sender_exit(reopened, task_id, row["tool_call_id"])
    expected = {"ticket_id": "T-1"} if exit_status == "succeeded" else RetryDecision.RECONCILE
    assert outcome == expected
    final = read_state(store)
    if exit_status == "succeeded":
        assert final == after_exit
    else:
        assert final == {**after_exit, "calls": [{**row, "status": "unknown"}]}
    assert run_ticket(reopened, task_id, attempt_id, fake.send) == expected
    assert read_state(store) == final
    assert fake.tickets() == tickets
