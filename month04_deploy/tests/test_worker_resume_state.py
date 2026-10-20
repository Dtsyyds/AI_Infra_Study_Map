import pytest

from month04_deploy.app.durable.worker import restore_progress


PAYLOAD = {"text": "crash lesson"}

COMPLETED = [
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


@pytest.mark.parametrize(
    ("count", "expected_state"),
    [
        (0, {"text": "crash lesson"}),
        (1, {"text": "crash lesson"}),
        (2, {"text": "echo:crash lesson"}),
        (3, {"answer": "echo:crash lesson"}),
    ],
)
def test_restore_each_checkpoint_boundary(count, expected_state):
    next_step, state = restore_progress(
        PAYLOAD,
        COMPLETED[:count],
    )

    assert next_step == count
    assert state == expected_state


@pytest.mark.parametrize(
    "steps",
    [
        [COMPLETED[1]],                 # 缺少第 0 步
        [COMPLETED[0], COMPLETED[2]],    # 中间缺少第 1 步
        [COMPLETED[0], COMPLETED[0]],    # 重复编号
    ],
)
def test_reject_noncontiguous_steps(steps):
    with pytest.raises(ValueError, match="连续"):
        restore_progress(PAYLOAD, steps)


def test_reject_changed_step_name():
    changed = {
        **COMPLETED[0],
        "step_name": "read_input_v2",
    }

    with pytest.raises(ValueError, match="名称"):
        restore_progress(PAYLOAD, [changed])