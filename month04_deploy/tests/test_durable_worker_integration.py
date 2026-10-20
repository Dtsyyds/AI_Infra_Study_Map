import os
import subprocess
import sys
from pathlib import Path

import pytest

from month04_deploy.app.durable.task_store import TaskStore


REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def run_worker(task_id, *extra_args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "month04_deploy.app.durable.worker",
            "--task-id",
            str(task_id),
            *extra_args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("crash_step", [0, 1, 2])
def test_resume_after_committed_step(crash_step):
    dsn = os.getenv("AGENT_DATABASE_URL")
    assert dsn, "已启用数据库测试，但缺少 AGENT_DATABASE_URL"

    store = TaskStore(dsn)

    text = f"integration-crash-{crash_step}"
    expected_result = {"answer": f"echo:{text}"}

    # 每个用例创建自己的任务，不复用手工实验任务。
    created = store.create_task(
        "demo.echo",
        {"text": text},
    )
    task_id = created["task_id"]
    print(f"crash_step={crash_step} task_id={task_id}")

    # 第一个独立进程：在指定步骤提交后退出。
    crashed = run_worker(
        task_id,
        "--crash-after-step",
        str(crash_step),
    )
    assert crashed.returncode == 86, (
        crashed.stdout + crashed.stderr
    )

    task_before = store.get_task(task_id)
    steps_before = store.get_completed_steps(task_id)

    assert task_before is not None
    assert task_before["status"] == "running"
    assert task_before["result"] is None

    assert [
        step["step_index"] for step in steps_before
    ] == list(range(crash_step + 1))

    expected_outputs = [
        {"text": text},
        {"text": f"echo:{text}"},
        expected_result,
    ]
    assert [
        step["result"] for step in steps_before
    ] == expected_outputs[:crash_step + 1]

    # run_worker 已等待旧进程退出，之后才启动恢复进程。
    resumed = run_worker(task_id, "--resume")
    assert resumed.returncode == 0, (
        resumed.stdout + resumed.stderr
    )

    task_after = store.get_task(task_id)
    steps_after = store.get_completed_steps(task_id)

    assert task_after is not None
    assert task_after["status"] == "succeeded"
    assert task_after["result"] == expected_result

    assert [
        step["step_index"] for step in steps_after
    ] == [0, 1, 2]

    assert [
        step["step_name"] for step in steps_after
    ] == [
        "read_input_v1",
        "build_echo_v1",
        "format_result_v1",
    ]

    assert [
        step["result"] for step in steps_after
    ] == expected_outputs

    # 恢复前已提交的记录必须原样保留，
    # 包括结果和 completed_at。
    assert steps_after[:len(steps_before)] == steps_before