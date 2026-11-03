"""用同一条 Task 行锁验证 Worker 确认取消和终态写回的先后顺序。"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable import worker
from month04_deploy.app.durable.task_store import CancelledAtCheckpoint, TaskStore
from month04_deploy.tests.test_task_cancel_request_integration import cancel_store as cancel_store
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_task_retry_integration import deadline_for_creation, wait_for_task_row_block
from month04_deploy.tests.test_worker_attempts_integration import read_snapshot, run_worker, store as store


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def assert_confirmed(store, row, pending, attempt, steps, version):
    assert row is not None
    task, attempts, saved = read_snapshot(store, pending["task_id"])
    assert task == row
    assert (task["status"], task["lease_version"], task["lease_expires_at"]) == ("cancelled", version, None)
    assert (task["result"], task["error"], task["next_retry_at"]) == (None, None, None)
    assert task["cancel_requested_at"] == pending["cancel_requested_at"]
    assert task["cancel_requested_at"] <= task["updated_at"]
    assert len(attempts) == attempt["attempt_no"]
    assert attempts[-1] == {**attempt, "status": "interrupted", "finished_at": task["updated_at"]}
    assert saved == steps


@pytest.mark.parametrize("raises_error", [False, True], ids=["step-result", "step-error"])
def test_cancel_during_step_discards_late_result_or_error(cancel_store, monkeypatch, capsys, raises_error):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "mid-step"})["task_id"]
    observed = {}
    original = worker.STEPS

    def request_during_work(state):
        pending = store.request_task_cancel(task_id)
        _, attempts, steps = read_snapshot(store, task_id)
        assert pending["status"] == "running" and [s["step_index"] for s in steps] == [0]
        observed.update(pending=pending, attempt=attempts[0], steps=steps)
        if raises_error:
            raise ValueError("取消请求已经提交")
        return original[1][1](state)

    monkeypatch.setattr(worker, "STEPS", (
        original[0], (original[1][0], request_during_work),
        (original[2][0], lambda _: pytest.fail("已取消的 Worker 执行了下一步")),
    ))
    monkeypatch.setattr(worker.os, "_exit", lambda _: pytest.fail("未保存的步骤触发了故障注入"))
    row = worker.run_task_once(store, task_id, crash_after_step=1)
    assert_confirmed(store, row, observed["pending"], observed["attempt"], observed["steps"], 1)
    output = capsys.readouterr().out
    assert "STEP_SAVED index=0" in output and "STEP_SAVED index=1" not in output
    assert "INJECTED_CRASH" not in output


@pytest.mark.parametrize("last_saved", [0, 2], ids=["between-steps", "before-success"])
def test_cancel_after_committed_checkpoint_keeps_the_whole_prefix(cancel_store, monkeypatch, last_saved):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "prefix"})["task_id"]
    original_save = store.try_save_completed_step
    observed = {}

    def save_then_request(task_id, step_index, step_name, result, **credentials):
        saved = original_save(task_id, step_index, step_name, result, **credentials)
        if step_index == last_saved:
            assert isinstance(saved, dict)
            pending = store.request_task_cancel(task_id)
            _, attempts, steps = read_snapshot(store, task_id)
            observed.update(pending=pending, attempt=attempts[0], steps=steps)
        return saved

    monkeypatch.setattr(store, "try_save_completed_step", save_then_request)
    if last_saved == 0:
        a, b, c = worker.STEPS
        monkeypatch.setattr(worker, "STEPS", (a, (b[0], lambda _: pytest.fail("取消后又启动了新步骤")), c))
    row = worker.run_task_once(store, task_id)
    assert [s["step_index"] for s in observed["steps"]] == list(range(last_saved + 1))
    assert_confirmed(store, row, observed["pending"], observed["attempt"], observed["steps"], 1)


def test_request_during_restore_stops_before_first_resumed_operation(cancel_store, monkeypatch):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "restore"})["task_id"]
    _, attempt = store.try_start_task_with_attempt(task_id)
    original_read = store.get_completed_steps
    pending = None

    def request_after_read(task_id):
        nonlocal pending
        steps = original_read(task_id)
        pending = store.request_task_cancel(task_id)
        return steps

    monkeypatch.setattr(store, "get_completed_steps", request_after_read)
    a, b, c = worker.STEPS
    monkeypatch.setattr(worker, "STEPS", ((a[0], lambda _: pytest.fail("恢复后启动了已取消的步骤")), b, c))
    row = worker.run_task_once(store, task_id, resume=True)
    assert pending is not None
    assert_confirmed(store, row, pending, attempt, [], 1)


def test_cli_reports_confirmed_cancel_before_resume(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "cli"})["task_id"]
    task, attempt = store.try_start_task_with_attempt(task_id)
    set_lease_remaining(store, task, timedelta(hours=1))
    pending = store.request_task_cancel(task_id)
    process = run_worker(store, task_id, "--resume")
    assert process.returncode == 0, process.stdout + process.stderr
    assert "CANCELLED" in process.stdout.splitlines()
    assert "SUCCEEDED" not in process.stdout and "STEP_SAVED" not in process.stdout
    assert_confirmed(store, store.get_task(task_id), pending, attempt, [], 1)


def test_retry_worker_confirms_request_on_the_new_attempt(cancel_store, monkeypatch):
    store = cancel_store
    task_id = store.create_task(
        "demo.echo", {"text": "retry"}, max_attempts=2, retry_deadline_at=deadline_for_creation(store),
    )["task_id"]
    a, attempt_a = store.try_start_task_with_attempt(task_id)
    old_id = attempt_a["attempt_id"]
    assert store.try_fail_task_with_attempt(
        task_id, old_id, {"message": "transient"}, expected_lease_version=a["lease_version"],
    ) is not None
    policy = dict(retryable_error=True, outcome_unknown=False, replay_safe=True)
    assert store.try_schedule_task_retry(task_id, old_id, delay_seconds=0, **policy)["next_retry_at"] is not None
    original_claim = store.try_retry_task_with_attempt
    observed = {}

    def request_after_claim(*args, **kwargs):
        task, attempt = original_claim(*args, **kwargs)
        observed.update(pending=store.request_task_cancel(task_id), attempt=attempt)
        return task, attempt

    monkeypatch.setattr(store, "try_retry_task_with_attempt", request_after_claim)
    row = worker.retry_task_once(store, task_id, old_id, **policy)
    assert_confirmed(store, row, observed["pending"], observed["attempt"], [], 2)
    assert read_snapshot(store, task_id)[1][0]["status"] == "failed"


def test_only_current_owner_can_confirm_after_takeover(cancel_store):
    store = cancel_store
    task_id = store.create_task(
        "demo.echo", {"text": "handoff"}, max_attempts=2, retry_deadline_at=deadline_for_creation(store),
    )["task_id"]
    a, attempt_a = store.try_start_task_with_attempt(task_id)
    pending = store.request_task_cancel(task_id)
    set_lease_remaining(store, a, timedelta(seconds=-1))
    before = read_snapshot(store, task_id)
    assert store.check_claim_before_step(task_id, attempt_a["attempt_id"], expected_lease_version=1) is None
    assert read_snapshot(store, task_id) == before

    b, attempt_b = store.try_take_over_expired_task(task_id, expected_lease_version=1)
    set_lease_remaining(store, b, timedelta(hours=1))
    before = read_snapshot(store, task_id)
    for aid, version in [(attempt_a["attempt_id"], 1), (attempt_b["attempt_id"], 1), (attempt_a["attempt_id"], 2)]:
        assert store.check_claim_before_step(task_id, aid, expected_lease_version=version) is None
        assert read_snapshot(store, task_id) == before
    row = store.check_claim_before_step(task_id, attempt_b["attempt_id"], expected_lease_version=2)
    assert_confirmed(store, row, pending, attempt_b, [], 2)
    assert read_snapshot(store, task_id)[1][0] == before[1][0]
    assert store.request_task_cancel(task_id) == row


def test_checkpoint_api_returns_confirmed_task_instead_of_a_saved_step(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "write"})["task_id"]
    task, attempt = store.try_start_task_with_attempt(task_id)
    pending = store.request_task_cancel(task_id)
    outcome = store.try_save_completed_step(
        task_id, 0, "read_input_v1", {"text": "write"},
        expected_lease_version=task["lease_version"], expected_attempt_id=attempt["attempt_id"],
    )
    assert isinstance(outcome, CancelledAtCheckpoint)
    assert_confirmed(store, outcome.task, pending, attempt, [], 1)


def test_rejected_attempt_update_rolls_back_confirmation_and_can_be_retried(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "rollback"})["task_id"]
    task, attempt = store.try_start_task_with_attempt(task_id)
    set_lease_remaining(store, task, timedelta(hours=1))
    pending = store.request_task_cancel(task_id)
    before = read_snapshot(store, task_id)
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION skip_confirmation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
        """)
        conn.execute("""
            CREATE TRIGGER skip_confirmation BEFORE UPDATE ON task_attempts
            FOR EACH ROW WHEN (NEW.status = 'interrupted') EXECUTE FUNCTION skip_confirmation()
        """)
    with pytest.raises(RuntimeError, match="Attempt"):
        store.check_claim_before_step(task_id, attempt["attempt_id"], expected_lease_version=1)
    assert read_snapshot(store, task_id) == before
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("DROP TRIGGER skip_confirmation ON task_attempts")
    row = store.check_claim_before_step(task_id, attempt["attempt_id"], expected_lease_version=1)
    assert_confirmed(store, row, pending, attempt, [], 1)


def test_legacy_success_cannot_overwrite_a_pending_cancel(cancel_store):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "legacy"})["task_id"]
    assert store.try_start_task(task_id) is not None
    assert store.request_task_cancel(task_id)["cancel_requested_at"] is not None
    before = read_snapshot(store, task_id)
    assert store.try_succeed_task(task_id, {"answer": "late"}) is None
    assert read_snapshot(store, task_id) == before


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
@pytest.mark.parametrize("first", ["request", "finish"])
def test_request_and_terminal_follow_the_committed_order(cancel_store, monkeypatch, first, terminal):
    store = cancel_store
    task_id = store.create_task("demo.echo", {"text": "race"})["task_id"]
    running, attempt = store.try_start_task_with_attempt(task_id)
    set_lease_remaining(store, running, timedelta(hours=1))
    tag = f"cancel_finish_{uuid4().hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    contender = TaskStore(make_conninfo(
        store.dsn, application_name=tag, options=f"{options} -c lock_timeout=15s -c statement_timeout=20s",
    ))

    def finish(s):
        method = s.try_succeed_task_with_attempt if terminal == "succeeded" else s.try_fail_task_with_attempt
        return method(task_id, attempt["attempt_id"], {"message": "original"}, expected_lease_version=1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with (
            psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as holder,
            psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5, autocommit=True) as monitor,
        ):
            # 两个业务方法都使用真实 SQL，测试仅控制第一个连接的提交时刻。
            with monkeypatch.context() as patch:
                patch.setattr(store, "_connect", lambda: nullcontext(holder))
                winner = store.request_task_cancel(task_id) if first == "request" else finish(store)
            assert winner is not None
            if first == "request":
                waiting = pool.submit(finish, contender)
            else:
                waiting = pool.submit(contender.request_task_cancel, task_id)
            wait_for_task_row_block(monitor, tag, holder.info.backend_pid, waiting)
        second = waiting.result(timeout=15)

    if first == "request":
        assert winner["status"] == "running"
        assert_confirmed(store, second, winner, attempt, [], 1)
    else:
        task, attempts, steps = read_snapshot(store, task_id)
        assert second == winner == task and task["cancel_requested_at"] is None
        assert task["status"] == attempts[0]["status"] == terminal and steps == []
        assert task["result" if terminal == "succeeded" else "error"] == {"message": "original"}
