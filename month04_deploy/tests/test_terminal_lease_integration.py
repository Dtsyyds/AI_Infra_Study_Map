"""用真实 PostgreSQL 验证 Task 和 Attempt 的终态写回。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from month04_deploy.app.durable import worker
from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.tests.test_task_lease_takeover_integration import (
    first_claim as first_claim,
    set_lease_remaining,
)
from month04_deploy.tests.test_task_lease_takeover_race_integration import (
    wait_for_both_workers,
)
from month04_deploy.tests.test_task_retry_integration import (
    deadline_for_creation,
    wait_for_task_row_block,
)
from month04_deploy.tests.test_worker_attempts_integration import read_snapshot, store as store, run_worker


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def finish(store, terminal, task_id, attempt_id, version):
    if terminal == "succeeded":
        return store.try_succeed_task_with_attempt(
            task_id, attempt_id, {"answer": "accepted"}, expected_lease_version=version
        )
    return store.try_fail_task_with_attempt(
        task_id, attempt_id, {"message": "accepted"}, expected_lease_version=version
    )


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
@pytest.mark.parametrize("remaining", [timedelta(seconds=-1), None], ids=["expired", "null"])
def test_terminal_write_requires_valid_expiry_without_a_successor(store, first_claim, terminal, remaining):
    task, attempt = first_claim
    task_id = task["task_id"]
    set_lease_remaining(store, task, remaining)
    before = read_snapshot(store, task_id)
    assert before[1] == [attempt]
    assert finish(store, terminal, task_id, attempt["attempt_id"], task["lease_version"]) is None
    assert read_snapshot(store, task_id) == before


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_only_the_new_owner_can_set_a_terminal_state(store, first_claim, terminal):
    a, attempt_a = first_claim
    task_id = a["task_id"]
    set_lease_remaining(store, a, timedelta(seconds=-1))
    claim_b = store.try_take_over_expired_task(task_id, expected_lease_version=a["lease_version"])
    assert claim_b is not None
    b, attempt_b = claim_b
    set_lease_remaining(store, b, timedelta(hours=1))

    other = store.create_task("demo.echo", {"text": "foreign"})
    foreign_claim = store.try_start_task_with_attempt(other["task_id"])
    assert foreign_claim is not None
    _, foreign = foreign_claim
    foreign_before = read_snapshot(store, other["task_id"])
    before = read_snapshot(store, task_id)
    for wrong_version, wrong_attempt in [
        (a["lease_version"], attempt_a["attempt_id"]),
        (a["lease_version"], attempt_b["attempt_id"]),
        (b["lease_version"], attempt_a["attempt_id"]),
        (b["lease_version"], foreign["attempt_id"]),
    ]:
        assert finish(store, terminal, task_id, wrong_attempt, wrong_version) is None
        assert read_snapshot(store, task_id) == before
    assert read_snapshot(store, other["task_id"]) == foreign_before

    row = finish(store, terminal, task_id, attempt_b["attempt_id"], b["lease_version"])
    assert row is not None
    task, attempts, steps = read_snapshot(store, task_id)
    assert task == row
    assert (row["status"], row["lease_version"], row["lease_expires_at"]) == (terminal, 2, None)
    assert row["result"] == ({"answer": "accepted"} if terminal == "succeeded" else None)
    assert row["error"] == (None if terminal == "succeeded" else {"message": "accepted"})
    assert attempts == [
        before[1][0],
        {**attempt_b, "status": terminal, "error": row["error"], "finished_at": row["updated_at"]},
    ]
    assert steps == before[2]
    assert finish(store, terminal, task_id, attempt_b["attempt_id"], b["lease_version"]) is None
    assert read_snapshot(store, task_id) == (row, attempts, steps)


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_terminal_checks_time_after_waiting_for_the_task_row(store, first_claim, terminal):
    task, attempt = first_claim
    task_id = task["task_id"]
    set_lease_remaining(store, task, timedelta(hours=1))
    before, attempts, steps = read_snapshot(store, task_id)
    tag = f"finish_wait_{task_id.hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    contender = TaskStore(make_conninfo(
        store.dsn, application_name=tag,
        options=f"{options} -c lock_timeout=15000 -c statement_timeout=20000",
    ))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with (
            psycopg.connect(store.dsn, row_factory=dict_row, connect_timeout=5) as holder,
            psycopg.connect(
                store.dsn, row_factory=dict_row, autocommit=True, connect_timeout=5
            ) as monitor,
        ):
            locked = holder.execute(
                "SELECT task_id FROM tasks WHERE task_id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            assert locked is not None
            future = pool.submit(
                finish, contender, terminal, task_id, attempt["attempt_id"], task["lease_version"]
            )
            waiting = wait_for_task_row_block(monitor, tag, holder.info.backend_pid, future)
            # 将到期时间设在请求开始之后、释放行锁之前。
            expiry = holder.execute(
                """
                UPDATE tasks SET lease_expires_at = clock_timestamp()
                WHERE task_id = %s RETURNING lease_expires_at
                """, (task_id,),
            ).fetchone()["lease_expires_at"]
            assert waiting["xact_start"] < expiry
        assert future.result(timeout=15) is None
    assert read_snapshot(store, task_id) == ({**before, "lease_expires_at": expiry}, attempts, steps)


def test_worker_expires_after_its_last_checkpoint(store, monkeypatch):
    task = store.create_task(
        "demo.echo", {"text": "last"}, max_attempts=2, retry_deadline_at=deadline_for_creation(store)
    )
    task_id = task["task_id"]
    original_save = store.try_save_completed_step
    at_expiry = None

    def expire_after_last_step(task_id, step_index, step_name, result, **credentials):
        nonlocal at_expiry
        saved = original_save(task_id, step_index, step_name, result, **credentials)
        assert saved is not None
        if step_index == len(worker.STEPS) - 1:
            running, _, _ = read_snapshot(store, task_id)
            set_lease_remaining(store, running, timedelta(seconds=-1))
            at_expiry = read_snapshot(store, task_id)
        return saved

    with monkeypatch.context() as patch:
        patch.setattr(store, "try_save_completed_step", expire_after_last_step)
        assert worker.run_task_once(store, task_id) is None
    assert at_expiry is not None
    assert read_snapshot(store, task_id) == at_expiry
    running, attempts, steps = at_expiry
    assert (running["status"], attempts[0]["status"]) == ("running", "running")
    assert [step["step_index"] for step in steps] == [0, 1, 2]

    claim_b = store.try_take_over_expired_task(task_id, expected_lease_version=running["lease_version"])
    assert claim_b is not None
    b, attempt_b = claim_b
    set_lease_remaining(store, b, timedelta(hours=1))
    # A 已返回。B 直接从完整检查点收尾。
    completed = worker.run_task_once(store, task_id, resume=True)
    assert completed is not None
    assert (completed["status"], completed["result"]) == ("succeeded", {"answer": "echo:last"})
    task_after, attempts_after, steps_after = read_snapshot(store, task_id)
    assert task_after == completed and steps_after == steps
    assert [a["status"] for a in attempts_after] == ["interrupted", "succeeded"]
    assert attempts_after[1]["attempt_id"] == attempt_b["attempt_id"]


def test_worker_discards_business_error_after_takeover(store, monkeypatch):
    created = store.create_task(
        "demo.echo", {"text": "late-error"}, max_attempts=2, retry_deadline_at=deadline_for_creation(store)
    )
    task_id = created["task_id"]
    after_takeover = None

    def raise_after_takeover(_state):
        nonlocal after_takeover
        task, _, _ = read_snapshot(store, task_id)
        set_lease_remaining(store, task, timedelta(seconds=-1))
        takeover = store.try_take_over_expired_task(task_id, expected_lease_version=task["lease_version"])
        assert takeover is not None
        after_takeover = read_snapshot(store, task_id)
        raise ValueError("A 的迟到错误")

    original = worker.STEPS
    monkeypatch.setattr(
        worker, "STEPS", (original[0], (original[1][0], raise_after_takeover), original[2])
    )
    assert worker.run_task_once(store, task_id) is None
    assert after_takeover is not None
    assert read_snapshot(store, task_id) == after_takeover
    task, attempts, steps = after_takeover
    assert (task["status"], task["lease_version"], task["error"]) == ("running", 2, None)
    assert [a["status"] for a in attempts] == ["interrupted", "running"]
    assert [step["step_index"] for step in steps] == [0]


def test_task_only_success_cannot_bypass_a_claim(store, first_claim):
    task, _ = first_claim
    task_id = task["task_id"]
    before = read_snapshot(store, task_id)
    assert store.try_succeed_task(task_id, {"answer": "unfenced"}) is None
    assert read_snapshot(store, task_id) == before
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute(
            "UPDATE tasks SET lease_version = 0, lease_expires_at = NULL WHERE task_id = %s", (task_id,)
        )
    before = read_snapshot(store, task_id)
    assert store.try_succeed_task(task_id, {"answer": "has an attempt"}) is None
    assert read_snapshot(store, task_id) == before

    legacy_id = store.create_task("demo.echo", {"text": "task-only"})["task_id"]
    assert store.try_start_task(legacy_id) is not None
    completed = store.try_succeed_task(legacy_id, {"answer": "legacy"})
    assert completed is not None and completed["status"] == "succeeded"

def test_take_over_task_once_keeps_checkpoint(store, first_claim, monkeypatch):
    first_task, first_attempt = first_claim
    task_id = first_task["task_id"]
    version = first_task["lease_version"]

    set_lease_remaining(store, first_task, timedelta(hours=1))
    step_zero = store.try_save_completed_step(
        task_id, 0, worker.STEPS[0][0], first_task["payload"],
        expected_lease_version=version,
        expected_attempt_id=first_attempt["attempt_id"],
    )
    assert isinstance(step_zero, dict)
    before = read_snapshot(store, task_id)

    def reject_lookup(*args, **kwargs):
        pytest.fail("接管后应使用自己领到的 Task 和 Attempt")

    monkeypatch.setattr(store, "get_task", reject_lookup)
    monkeypatch.setattr(store, "get_running_attempt", reject_lookup)

    # A 的租约仍有效，B 的接管请求应当无副作用。
    assert worker.take_over_task_once(store, task_id, expected_lease_version=version) is None
    assert read_snapshot(store, task_id) == before

    # 让 A 的租约到期，B 应从第 1 步继续。
    set_lease_remaining(store, first_task, timedelta(seconds=-1))
    finished = worker.take_over_task_once(store, task_id, expected_lease_version=version)

    task, attempts, steps = read_snapshot(store, task_id)
    assert finished == task and task["status"] == "succeeded"
    assert task["lease_version"] == version + 1
    assert task["result"] == {"answer": "echo:takeover"}
    assert [(a["attempt_no"], a["status"]) for a in attempts] == [
        (1, "interrupted"), (2, "succeeded")
    ]
    assert [s["step_index"] for s in steps] == [0, 1, 2]
    assert steps[0] == step_zero

def test_cli_takeover_after_successor_crash(store, first_claim):
    task_a, _ = first_claim
    task_id = task_a["task_id"]
    version_a = task_a["lease_version"]
    set_lease_remaining(store, task_a, timedelta(seconds=-1))

    b = run_worker(
        store, task_id, "--take-over-version", str(version_a), "--crash-after-step", "0"
    )
    assert b.returncode == 86, b.stdout + b.stderr
    task_b, attempts_b, prefix = read_snapshot(store, task_id)
    assert (task_b["lease_version"], task_b["status"]) == (version_a + 1, "running")
    assert [a["status"] for a in attempts_b] == ["interrupted", "running"]
    assert [s["step_index"] for s in prefix] == [0]

    set_lease_remaining(store, task_b, timedelta(seconds=-1))
    before = read_snapshot(store, task_id)
    stale = run_worker(store, task_id, "--take-over-version", str(version_a))
    assert stale.returncode == 2 and "NOT_ELIGIBLE" in stale.stdout, stale.stdout + stale.stderr
    assert read_snapshot(store, task_id) == before

    c = run_worker(store, task_id, "--take-over-version", str(task_b["lease_version"]))
    assert c.returncode == 0, c.stdout + c.stderr
    assert "RESTORED next_step=1" in c.stdout

    task, attempts, steps = read_snapshot(store, task_id)
    assert (task["status"], task["lease_version"], task["result"]) == (
        "succeeded", version_a + 2, {"answer": "echo:takeover"},
    )
    assert [a["status"] for a in attempts] == ["interrupted", "interrupted", "succeeded"]
    assert [s["step_index"] for s in steps] == [0, 1, 2]
    assert steps[:1] == prefix

def test_two_cli_workers_compete_for_one_expired_lease(store, first_claim):
    task_a, attempt_a = first_claim
    task_id = task_a["task_id"]
    version = task_a["lease_version"]
    set_lease_remaining(store, task_a, timedelta(seconds=-1))

    tag = f"cli_takeover_{task_id.hex}"
    options = conninfo_to_dict(store.dsn).get("options", "")
    cli_store = TaskStore(make_conninfo(
        store.dsn,
        application_name=tag,
        options=f"{options} -c lock_timeout=20s -c statement_timeout=30s",
    ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        with psycopg.connect(store.dsn, connect_timeout=5) as holder:
            locked = holder.execute(
                "SELECT task_id FROM tasks WHERE task_id = %s FOR UPDATE",
                (task_id,),
            ).fetchone()
            assert locked == (task_id,)

            futures = [
                pool.submit(run_worker, cli_store, task_id, "--take-over-version", str(version))
                for _ in range(2)
            ]
            wait_for_both_workers(store.dsn, tag, holder.info.backend_pid, futures)

        # 到这里 holder 已经释放行锁，两个子进程才能完成接管判断。
        results = [future.result(timeout=30) for future in futures]

    winner, loser = sorted(results, key=lambda p: p.returncode)
    assert (winner.returncode, loser.returncode) == (0, 2), [
        (p.returncode, p.stdout, p.stderr) for p in results
    ]
    assert "SUCCEEDED" in winner.stdout
    assert "NOT_ELIGIBLE:" in loser.stdout and "STEP_SAVED" not in loser.stdout

    task, attempts, steps = read_snapshot(store, task_id)
    assert (task["status"], task["lease_version"], task["result"]) == (
        "succeeded", version + 1, {"answer": "echo:takeover"},
    )
    assert attempts[0]["attempt_id"] == attempt_a["attempt_id"]
    assert [(a["attempt_no"], a["status"]) for a in attempts] == [
        (1, "interrupted"), (2, "succeeded"),
    ]
    assert [s["step_index"] for s in steps] == [0, 1, 2]