from unittest.mock import call, create_autospec
from uuid import uuid4

import psycopg
import pytest

from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.app.durable.worker import run_task_once


@pytest.fixture
def worker_case():
    task_id = uuid4()
    attempt_id = uuid4()
    store = create_autospec(TaskStore, instance=True)

    task = {
        "task_id": task_id,
        "task_type": "demo.echo",
        "payload": {"text": "hello"},
        "status": "running",
        "lease_version": 1,
    }

    attempt = {
        "attempt_id": attempt_id,
        "task_id": task_id,
        "attempt_no": 1,
        "status": "running",
    }
    store.try_start_task_with_attempt.return_value = (task, attempt)
    store.try_save_completed_step.return_value = {"task_id": task_id}
    store.try_succeed_task_with_attempt.return_value = {
        **task,
        "status": "succeeded",
        "result": {"answer": "echo:hello"},
        "error": None,
    }

    return store, task_id, attempt_id


def test_worker_completes_three_steps(worker_case):
    store, task_id, attempt_id = worker_case

    finished = run_task_once(store, task_id)

    assert finished["status"] == "succeeded"
    assert finished["result"] == {"answer": "echo:hello"}

    store.try_start_task_with_attempt.assert_called_once_with(task_id)

    store.try_save_completed_step.assert_has_calls(
        [
            call(
                task_id,
                0,
                "read_input_v1",
                {"text": "hello"},
                expected_lease_version=1,
                expected_attempt_id=attempt_id,
            ),
            call(
                task_id,
                1,
                "build_echo_v1",
                {"text": "echo:hello"},
                expected_lease_version=1,
                expected_attempt_id=attempt_id,
            ),
            call(
                task_id,
                2,
                "format_result_v1",
                {"answer": "echo:hello"},
                expected_lease_version=1,
                expected_attempt_id=attempt_id,
            ),
        ]
    )
    assert store.try_save_completed_step.call_count == 3

    store.try_succeed_task_with_attempt.assert_called_once_with(
        task_id,
        attempt_id,
        {"answer": "echo:hello"},
        expected_lease_version=1,
    )

    # 验证认领、保存步骤、最终完成的调用顺序。
    assert [item[0] for item in store.mock_calls] == [
        "try_start_task_with_attempt",
        "try_save_completed_step",
        "try_save_completed_step",
        "try_save_completed_step",
        "try_succeed_task_with_attempt",
    ]


def test_worker_does_not_proceed_without_claim(worker_case):
    store, task_id, _ = worker_case
    store.try_start_task_with_attempt.return_value = None

    assert run_task_once(store, task_id) is None

    store.try_start_task_with_attempt.assert_called_once_with(task_id)
    store.try_save_completed_step.assert_not_called()
    store.try_succeed_task_with_attempt.assert_not_called()


def test_rejected_step_stops_worker_without_recording_an_outcome(worker_case, capsys):
    store, task_id, attempt_id = worker_case
    store.try_save_completed_step.return_value = None

    assert run_task_once(store, task_id) is None
    store.try_save_completed_step.assert_called_once_with(
        task_id, 0, "read_input_v1", {"text": "hello"},
        expected_lease_version=1, expected_attempt_id=attempt_id,
    )
    store.save_completed_step.assert_not_called()
    store.try_fail_task_with_attempt.assert_not_called()
    store.try_succeed_task_with_attempt.assert_not_called()
    assert "STEP_SAVED" not in capsys.readouterr().out


def test_step_save_error_prevents_task_success(worker_case):
    store, task_id, _ = worker_case
    store.try_save_completed_step.side_effect = (
        psycopg.OperationalError("step commit failed")
    )

    with pytest.raises(
        psycopg.OperationalError,
        match="step commit failed",
    ):
        run_task_once(store, task_id)

    assert store.try_save_completed_step.call_count == 1
    store.try_succeed_task_with_attempt.assert_not_called()


def test_lost_completion_claim_stops_worker(worker_case):
    store, task_id, attempt_id = worker_case
    store.try_succeed_task_with_attempt.return_value = None

    assert run_task_once(store, task_id) is None
    store.try_fail_task_with_attempt.assert_not_called()

    store.try_succeed_task_with_attempt.assert_called_once_with(
        task_id,
        attempt_id,
        {"answer": "echo:hello"},
        expected_lease_version=1,
    )
