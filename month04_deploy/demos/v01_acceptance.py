"""用同一个 demo.echo Task 演示请求重放、Worker 进程退出和断点恢复。"""

import os

import pytest
from fastapi.testclient import TestClient

from month04_deploy.app.api import create_app
from month04_deploy.tests.test_durable_worker_integration import run_worker
from month04_deploy.tests.test_tool_call_idempotency_integration import (
    read_state,
    store as store,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库演示",
)


def submit(client, key, text):
    return client.post(
        "/v1/tasks",
        headers={"Idempotency-Key": key},
        json={"task_type": "demo.echo", "payload": {"text": text}},
    )


def test_v01_acceptance(store, monkeypatch):
    # API 和三个 Worker 子进程都使用夹具创建的同一个临时 schema。
    monkeypatch.setenv("AGENT_DATABASE_URL", store.dsn)
    key, text = "v01-request-a", "v01"
    expected_result = {"answer": "echo:v01"}

    with TestClient(create_app()) as first_client:
        first = submit(first_client, key, text)
        assert first.status_code == 202, first.text
        # 模拟调用方丢失首次回执。接下来的请求不使用首次响应中的 task_id。

    submitted = read_state(store)
    original, = submitted["tasks"]
    task_id = original["task_id"]
    assert original["status"] == "queued"
    assert original["payload"] == {"text": text}
    assert original["idempotency_key"] == key
    location = f"/v1/tasks/{task_id}"

    with TestClient(create_app()) as client:
        replay = submit(client, key, text)
        assert replay.status_code == 202, replay.text
        assert replay.json()["task_id"] == str(task_id)
        assert replay.headers["Location"] == replay.json()["status_url"] == location

        conflict = submit(client, key, "edited")
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"] == "idempotency_conflict"
        assert read_state(store) == submitted
        print(f"提交  task_id={task_id}  同 key 同内容={replay.status_code}  "
              f"同 key 改内容={conflict.status_code}  Task 数={len(submitted['tasks'])}")

        crashed = run_worker(task_id, "--crash-after-step", "0")
        assert crashed.returncode == 86, crashed.stdout + crashed.stderr
        paused = read_state(store)
        paused_task, = paused["tasks"]
        attempt, = paused["attempts"]
        step_zero, = paused["steps"]
        assert paused_task["status"] == attempt["status"] == "running"
        assert attempt["task_id"] == step_zero["task_id"] == task_id
        assert attempt["attempt_no"] == 1
        assert step_zero["step_index"] == 0
        assert step_zero["result"] == {"text": text}
        print(f"退出  code={crashed.returncode}  attempt_id={attempt['attempt_id']}  "
              f"第 0 步已提交于 {step_zero['completed_at'].isoformat()}")

        # run_worker 已等到旧进程退出，此时才允许人工恢复。
        resumed = run_worker(task_id, "--resume")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        done = read_state(store)
        final_attempt, = done["attempts"]
        assert final_attempt["attempt_id"] == attempt["attempt_id"]
        assert final_attempt["status"] == "succeeded"
        indices = [row["step_index"] for row in done["steps"]]
        assert indices == [0, 1, 2]
        assert done["steps"][0] == step_zero  # 包括 result 和 completed_at

        view = client.get(location)
        assert view.status_code == 200, view.text
        assert view.json()["status"] == "succeeded"
        assert view.json()["result"] == expected_result
        print(f"恢复  code={resumed.returncode}  attempt_id={final_attempt['attempt_id']}  "
              f"步骤={indices}  第 0 步原样保留  result={view.json()['result']}")

        terminal = submit(client, key, text)
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["task_id"] == str(task_id)
        assert terminal.json()["result"] == expected_result
        repeated = run_worker(task_id)
        assert repeated.returncode == 2, repeated.stdout + repeated.stderr
        assert read_state(store) == done
        print(f"重放  终态 POST={terminal.status_code}  Worker code={repeated.returncode}  "
              f"Attempt 数={len(done['attempts'])}  步骤数={len(done['steps'])}")

        other = submit(client, "v01-request-b", text)
        assert other.status_code == 202, other.text
        other_id = other.json()["task_id"]
        assert other_id != str(task_id)
        ids = {str(row["task_id"]) for row in read_state(store)["tasks"]}
        assert ids == {str(task_id), other_id}
        print(f"换 key  新 task_id={other_id}  HTTP={other.status_code}")
