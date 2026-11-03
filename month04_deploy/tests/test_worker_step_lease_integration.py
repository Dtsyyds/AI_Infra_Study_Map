"""在 Worker 计算步骤的途中接管，检查迟到的写回。"""

import os
from datetime import timedelta

import pytest

from month04_deploy.app.durable import worker
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_task_retry_integration import deadline_for_creation
from month04_deploy.tests.test_worker_attempts_integration import (
    read_snapshot,
    store as store,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def test_worker_discards_result_if_task_is_taken_over_during_step(store, monkeypatch, capsys):
    created = store.create_task(
        "demo.echo", {"text": "handoff"},
        max_attempts=2, retry_deadline_at=deadline_for_creation(store),
    )
    task_id = created["task_id"]
    after_takeover = None

    def finish_old_computation(state):
        nonlocal after_takeover
        assert state == {"text": "handoff"}
        task, attempts, steps = read_snapshot(store, task_id)
        assert task["lease_version"] == 1
        assert [a["status"] for a in attempts] == ["running"]
        assert [step["step_index"] for step in steps] == [0]
        set_lease_remaining(store, task, timedelta(seconds=-1))
        claim = store.try_take_over_expired_task(task_id, expected_lease_version=1)
        assert claim is not None
        new_task, new_attempt = claim
        assert (new_task["lease_version"], new_attempt["attempt_no"]) == (2, 2)
        set_lease_remaining(store, new_task, timedelta(hours=1))
        after_takeover = read_snapshot(store, task_id)
        return {"text": "A-late"}

    def unexpected_next_step(_state):
        pytest.fail("旧 Worker 继续执行了后续步骤")

    def unexpected_exit(code):
        pytest.fail(f"旧 Worker 在被拒绝的步骤上触发退出 {code}")

    original_steps = worker.STEPS
    with monkeypatch.context() as patch:
        patch.setattr(worker, "STEPS", (
            original_steps[0],
            (original_steps[1][0], finish_old_computation),
            (original_steps[2][0], unexpected_next_step),
        ))
        patch.setattr(worker.os, "_exit", unexpected_exit)
        assert worker.run_task_once(store, task_id, crash_after_step=1) is None

    log = capsys.readouterr().out
    assert "STEP_SAVED index=0" in log
    assert "STEP_SAVED index=1" not in log
    assert "INJECTED_CRASH" not in log
    assert after_takeover is not None
    assert read_snapshot(store, task_id) == after_takeover
    running, attempts, prefix = after_takeover
    assert (running["status"], running["lease_version"]) == ("running", 2)
    assert [a["status"] for a in attempts] == ["interrupted", "running"]
    assert [step["step_index"] for step in prefix] == [0]

    # A 的调用已经返回，人工恢复当前的 Attempt #2。
    finished = worker.run_task_once(store, task_id, resume=True)
    assert finished is not None
    assert (finished["status"], finished["result"]) == ("succeeded", {"answer": "echo:handoff"})
    task, final_attempts, steps = read_snapshot(store, task_id)
    assert task == finished
    assert len(final_attempts) == 2
    assert final_attempts[0] == attempts[0]
    assert final_attempts[1]["attempt_id"] == attempts[1]["attempt_id"]
    assert final_attempts[1]["status"] == "succeeded"
    assert [s["step_index"] for s in steps] == [0, 1, 2]
    assert steps[:1] == prefix
    assert steps[1]["result"] == {"text": "echo:handoff"}
