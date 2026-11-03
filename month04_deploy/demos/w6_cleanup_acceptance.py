"""在本机 Linux Docker 和临时 PostgreSQL schema 中验收超时清理与执行名额。"""

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from month04_deploy.demos import w6_container_timeout as lab
from month04_deploy.tests.test_task_execution_capacity_claims_integration import (
    one_slot_store as one_slot_store,
)
from month04_deploy.tests.test_task_lease_takeover_integration import set_lease_remaining
from month04_deploy.tests.test_worker_attempts_integration import store as store


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1" or os.getenv("AGENT_RUN_DOCKER_TESTS") != "1",
    reason="验收需要 AGENT_RUN_DB_TESTS=1 和 AGENT_RUN_DOCKER_TESTS=1",
)

TASK_LABEL = "ai-infra-study.w6.task"
ATTEMPT_LABEL = "ai-infra-study.w6.attempt"


def confirm_stopped_attempt(store, task_id, attempt_id, probe):
    """根据启动时观察到的容器和进程，尝试确认这一个 Attempt 的清理。"""
    labels_json = lab.docker(
        "inspect", "--type", "container", "--format", "{{json .Config.Labels}}", probe.cid,
    )
    labels = json.loads(labels_json) or {}
    if labels.get(TASK_LABEL) != str(task_id) or labels.get(ATTEMPT_LABEL) != str(attempt_id):
        raise ValueError("容器标签与待确认的 Task 或 Attempt 不匹配")

    final, group_end = lab.stopped_evidence(probe)
    confirmed = store.try_confirm_task_cleanup(task_id, attempt_id)
    print(f"cleanup cid={probe.cid[:12]} attempt_id={attempt_id} exit={final['ExitCode']} "
          f"writer_exited=yes cgroup_end={group_end} db_updated={int(confirmed is not None)}",
          flush=True)
    return confirmed


def assert_b_waits(store, pending_a, queued_b):
    assert store.get_task(pending_a["task_id"]) == pending_a
    b = queued_b["task_id"]
    assert store.try_start_task_with_attempt(b) is None
    assert store.get_task(b) == queued_b
    assert store.get_task_attempts(b) == []


def test_cleanup_evidence_releases_one_execution_slot(one_slot_store, monkeypatch):
    store = one_slot_store
    os.close(lab.open_pidfd(os.getpid()))
    image = lab.local_image_id()
    a = store.create_task("demo.echo", {"text": "A"})["task_id"]
    queued_b = store.create_task("demo.echo", {"text": "B"})
    b = queued_b["task_id"]
    task, attempt = store.try_start_task_with_attempt(a)
    attempt_id = attempt["attempt_id"]
    # 本用例隔离 Docker 启动时间，只延长临时 schema 中 A 的测试租约。
    set_lease_remaining(store, task, timedelta(minutes=2))

    with TemporaryDirectory(prefix="month04-w6-db-", dir=Path.home()) as tmp:
        out = Path(tmp) / "evidence"
        out.mkdir()
        out.chmod(0o777)
        with lab.running_probe(image, out, f"{TASK_LABEL}={a}", f"{ATTEMPT_LABEL}={attempt_id}") as probe:
            lab.expect_host_timeout(probe.cid)
            pending = store.try_fail_task_with_attempt(
                a, attempt_id, {"type": "ToolTimeout"}, expected_lease_version=task["lease_version"],
                cleanup_pending=True,
            )
            assert pending is not None
            assert (pending["status"], pending["cleanup_pending"], pending["cleanup_attempt_id"]) == (
                "failed", True, attempt_id,
            )
            a_history = store.get_task_attempts(a)
            assert [(r["attempt_id"], r["status"]) for r in a_history] == [(attempt_id, "failed")]
            assert lab.populated(probe.group) == 1
            with pytest.raises(RuntimeError, match="尚未观察到容器退出"):
                confirm_stopped_attempt(store, a, attempt_id, probe)
            assert_b_waits(store, pending, queued_b)
            print("timeout A=failed cleanup_pending=yes cgroup_populated=1 B=queued", flush=True)

            try:
                lab.docker("stop", "-t", "1", probe.cid)
            except subprocess.TimeoutExpired:
                print("stop_reply=unknown", flush=True)
            # 这里先等到真实容器退出。即使知道退出码，下面仍需要独立的清理证据。
            assert int(lab.docker("wait", probe.cid)) == 137
            (out / "go").touch()
            with pytest.raises(ValueError, match="容器标签"):
                confirm_stopped_attempt(store, a, uuid4(), probe)
            with pytest.raises(ValueError, match="容器标签"):
                confirm_stopped_attempt(store, b, attempt_id, probe)

            def unreadable(_group):
                raise PermissionError("注入 cgroup.events 读取失败")

            with monkeypatch.context() as fault:
                fault.setattr(lab, "populated", unreadable)
                with pytest.raises(PermissionError, match="注入"):
                    confirm_stopped_attempt(store, a, attempt_id, probe)
            assert_b_waits(store, pending, queued_b)
            print("evidence_read=injected_error A_pending=yes B=queued", flush=True)

            assert confirm_stopped_attempt(store, a, attempt_id, probe) == a
            assert not (out / "late").exists()
            released = {**pending, "cleanup_pending": False, "cleanup_attempt_id": None}
            assert store.get_task(a) == released
            b_task, b_attempt = store.try_start_task_with_attempt(b)
            assert b_task["status"] == b_attempt["status"] == "running"
            assert confirm_stopped_attempt(store, a, attempt_id, probe) is None
            assert store.get_task(a) == released
            assert store.get_task_attempts(a) == a_history
            assert store.get_task(b) == b_task
            assert store.get_task_attempts(b) == [b_attempt]
            print("released A=failed B=running duplicate_update=0 late_file=no", flush=True)
