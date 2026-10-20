import pytest

from task_state import TaskStatus, validate_task_transition


def test_queued_can_start():
    result = validate_task_transition(
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    )

    assert result is None


def test_queued_cannot_succeed_directly():
    with pytest.raises(ValueError):
        validate_task_transition(
            TaskStatus.QUEUED,
            TaskStatus.SUCCEEDED,
        )


def test_running_can_return_to_queue():
    # 这里只验证状态图允许这条路径。
    # 是否满足重试条件，由其他逻辑负责。
    result = validate_task_transition(
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
    )

    assert result is None


@pytest.mark.parametrize(
    "current",
    [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ],
)
@pytest.mark.parametrize(
    "target",
    list(TaskStatus),
)
def test_terminal_state_rejects_every_transition(
    current,
    target,
):
    with pytest.raises(ValueError):
        validate_task_transition(current, target)