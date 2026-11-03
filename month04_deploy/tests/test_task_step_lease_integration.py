"""在 PostgreSQL 中验证步骤写入使用领取时的租约版本和 Attempt。"""

import os
from datetime import timedelta

import psycopg
import pytest

from month04_deploy.tests.test_task_lease_takeover_integration import (
    first_claim as first_claim,
    set_lease_remaining,
)
from month04_deploy.tests.test_worker_attempts_integration import (
    read_snapshot,
    store as store,
)


pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_DB_TESTS") != "1",
    reason="设置 AGENT_RUN_DB_TESTS=1 才运行真实数据库测试",
)


def save_step(store, task_id, version, attempt_id, result):
    return store.try_save_completed_step(
        task_id, 1, "build_echo_v1", result,
        expected_lease_version=version,
        expected_attempt_id=attempt_id,
    )


@pytest.fixture
def second_claim(store, first_claim):
    first_task, _ = first_claim
    set_lease_remaining(store, first_task, timedelta(seconds=-1))
    claim = store.try_take_over_expired_task(
        first_task["task_id"], expected_lease_version=first_task["lease_version"]
    )
    assert claim is not None
    task, attempt = claim
    assert (task["lease_version"], attempt["attempt_no"]) == (2, 2)
    set_lease_remaining(store, task, timedelta(hours=1))
    return claim


def test_stale_step_writer_leaves_the_position_for_the_new_owner(
    store, first_claim, second_claim
):
    old_task, old_attempt = first_claim
    new_task, new_attempt = second_claim
    task_id = old_task["task_id"]
    before = read_snapshot(store, task_id)
    assert before[2] == []

    # B 已接管。A 先写之前算好的步骤 1，B 随后写同一位置。
    stale = save_step(
        store, task_id, old_task["lease_version"], old_attempt["attempt_id"], {"text": "A"}
    )
    assert stale is None
    assert read_snapshot(store, task_id) == before

    result = {"text": "B", "nested": [1, False, None]}
    saved = save_step(
        store, task_id, new_task["lease_version"], new_attempt["attempt_id"], result
    )
    assert saved is not None
    assert (saved["task_id"], saved["step_index"], saved["step_name"]) == (
        task_id, 1, "build_echo_v1"
    )
    assert saved["result"] == result
    assert saved["completed_at"] is not None
    assert read_snapshot(store, task_id) == (before[0], before[1], [saved])


@pytest.mark.parametrize("mismatch", ["version", "interrupted-attempt", "other-task"])
def test_step_requires_each_part_of_the_claim(store, first_claim, second_claim, mismatch):
    old_task, old_attempt = first_claim
    task, attempt = second_claim
    task_id = task["task_id"]
    version = task["lease_version"]
    attempt_id = attempt["attempt_id"]
    if mismatch == "version":
        version = old_task["lease_version"]
    elif mismatch == "interrupted-attempt":
        attempt_id = old_attempt["attempt_id"]
    else:
        other = store.create_task("demo.echo", {"text": "other"})
        other_claim = store.try_start_task_with_attempt(other["task_id"])
        assert other_claim is not None
        attempt_id = other_claim[1]["attempt_id"]

    before = read_snapshot(store, task_id)
    assert save_step(store, task_id, version, attempt_id, {"text": "late"}) is None
    assert read_snapshot(store, task_id) == before


@pytest.mark.parametrize("remaining", [timedelta(seconds=-1), None], ids=["expired", "null"])
def test_step_refused_without_live_lease_even_before_takeover(store, first_claim, remaining):
    task, attempt = first_claim
    set_lease_remaining(store, task, remaining)
    task_id = task["task_id"]
    before = read_snapshot(store, task_id)
    assert save_step(
        store, task_id, task["lease_version"], attempt["attempt_id"], {"text": "late"}
    ) is None
    assert read_snapshot(store, task_id) == before


def test_same_owner_cannot_overwrite_a_saved_step(store, first_claim):
    task, attempt = first_claim
    set_lease_remaining(store, task, timedelta(hours=1))
    args = (store, task["task_id"], task["lease_version"], attempt["attempt_id"])
    saved = save_step(*args, {"text": "first"})
    assert saved is not None
    before = read_snapshot(store, task["task_id"])
    assert before[2] == [saved]
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        save_step(*args, {"text": "second"})
    assert caught.value.diag.constraint_name == "task_steps_pkey"
    assert read_snapshot(store, task["task_id"]) == before


def test_insert_returning_no_step_raises_runtime_error(store, first_claim):
    task, attempt = first_claim
    set_lease_remaining(store, task, timedelta(hours=1))
    # 只在夹具的随机 schema 内模拟 INSERT 成功执行但 RETURNING 无行。
    with psycopg.connect(store.dsn, connect_timeout=5) as conn:
        conn.execute("""
            CREATE FUNCTION skip_step_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$
        """)
        conn.execute("""
            CREATE TRIGGER skip_step_insert BEFORE INSERT ON task_steps
            FOR EACH ROW EXECUTE FUNCTION skip_step_insert()
        """)
    before = read_snapshot(store, task["task_id"])
    with pytest.raises(RuntimeError):
        save_step(
            store, task["task_id"], task["lease_version"], attempt["attempt_id"], {"text": "B"}
        )
    assert read_snapshot(store, task["task_id"]) == before
