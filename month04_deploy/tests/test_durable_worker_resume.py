from unittest.mock import call, create_autospec
from uuid import uuid4

import pytest

from month04_deploy.app.durable.task_store import TaskStore
from month04_deploy.app.durable.worker import run_task_once


@pytest.mark.parametrize("completed_count", [2, 3])
def test_resume_skips_saved_steps(completed_count):
    store = create_autospec(TaskStore, instance=True)
    task_id = uuid4()
    attempt_id = uuid4()

    saved_steps = [
        {
            "step_index": 0,
            "step_name": "read_input_v1",
            "result": {"text": "crash lesson"},
        },
        {
            "step_index": 1,
            "step_name": "build_echo_v1",
            "result": {"text": "echo:crash lesson"},
        },
        {
            "step_index": 2,
            "step_name": "format_result_v1",
            "result": {"answer": "echo:crash lesson"},
        },
    ]

    task = {
        "task_id": task_id,
        "task_type": "demo.echo",
        "payload": {"text": "crash lesson"},
        "status": "running",
    }
    final_result = {"answer": "echo:crash lesson"}

    store.get_task.return_value = task
    store.get_running_attempt.return_value = {
        "attempt_id": attempt_id,
        "task_id": task_id,
        "attempt_no": 1,
        "status": "running",
    }
    store.get_completed_steps.return_value = (
        saved_steps[:completed_count]
    )
    store.try_succeed_task_with_attempt.return_value = {
        **task,
        "status": "succeeded",
        "result": final_result,
    }

    finished = run_task_once(
        store,
        task_id,
        resume=True,
    )

    assert finished["status"] == "succeeded"
    assert finished["result"] == final_result

    store.get_task.assert_called_once_with(task_id)
    store.get_running_attempt.assert_called_once_with(task_id)
    store.get_completed_steps.assert_called_once_with(task_id)
    store.try_start_task_with_attempt.assert_not_called()

    expected_saves = (
        [
            call(
                task_id,
                2,
                "format_result_v1",
                final_result,
            )
        ]
        if completed_count == 2
        else []
    )

    assert (
        store.save_completed_step.call_args_list
        == expected_saves
    )
    store.try_succeed_task_with_attempt.assert_called_once_with(
        task_id,
        attempt_id,
        final_result,
    )
