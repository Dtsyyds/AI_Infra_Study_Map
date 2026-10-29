"""Real PG, synthetic candidate DATA. Does not execute a candidate or prove a repair."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from config.settings import Settings, settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.identity import Principal
from core.contracts.repair import Patch
from core.contracts.task import TaskRequest
from core.errors import LeaseLost, RuntimeFault
from runtime.repair.manifest import tree_hash
from runtime.repair.persistence import reserve_model_call
from runtime.repair.workspace import apply_patch
from runtime.storage.store import Store, digest


async def admitted():
    store = Store(settings.database_url.get_secret_value())
    principal = Principal(principal_id="repair-test-" + str(uuid4()))
    task = await store.submit(
        principal,
        TaskRequest(task_type="code_repair", case_id="case-01", session_id=str(uuid4())),
        str(uuid4()),
    )
    # Explicit storage fixture: no claim that baseline tests actually ran.
    async with store.connection() as conn:
        await conn.execute(
            "UPDATE arc_ref.tasks SET context=jsonb_set(context,'{baseline}',%s) WHERE task_id=%s",
            (
                Jsonb({"verdict": {"status": "FAIL", "failed_ids": ["test_case.Case.test_bug"]}}),
                task["task_id"],
            ),
        )
    execution = await store.claim("storage-probe", 60, task["task_id"])
    assert execution
    args = {"candidate_no": 1}
    action = PreparedAction(
        task_id=execution.task_id,
        step_no=0,
        tool_call_id=str(uuid4()),
        tool="repair.generate",
        arguments=args,
        arguments_digest=digest(args),
        effect_class=EffectClass.READ_ONLY,
    )
    return store, principal, execution, action


async def test_candidate_reservation_recovery_and_patch_atomicity():
    store, p, ex, action = await admitted()
    await store.prepare(ex, action)
    await store.prepare(ex, action)
    await store.dispatch(ex, action)
    from core.contracts.repair import CaseManifest

    manifest = CaseManifest.model_validate(ex.context["manifest"])
    patch = Patch.model_validate(
        {
            "edits": [
                {"path": "src/solution.py", "old": "max(low, x)", "new": "min(high, max(low, x))"}
            ]
        }
    )
    files = apply_patch(manifest, ex.context["base_files"], patch)
    result = {"candidate_no": 1, "patch": patch.model_dump(), "candidate_hash": tree_hash(files)}
    with pytest.raises(RuntimeFault, match="CANDIDATE_HASH_MISMATCH"):
        await store.checkpoint(
            ex, action, {**result, "candidate_hash": "0" * 64}, ex.context, b"bad"
        )
    diag = await store.diagnostics(ex.task_id)
    assert (
        diag["artifacts"] == []
        and diag["steps"] == []
        and diag["repair_candidates"][0]["patch"] is None
    )
    await store.checkpoint(
        ex, action, result, {**ex.context, "candidate": result}, b"patch-data-storage-probe"
    )
    async with store.connection() as conn:
        await conn.execute(
            "UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s",
            (ex.task_id,),
        )
    recovered = await store.claim("replacement", 60, ex.task_id)
    assert recovered and recovered.attempt_id != ex.attempt_id and recovered.checkpoint == 0
    diag = await store.diagnostics(ex.task_id)
    assert len(diag["repair_candidates"]) == 1
    committed_audit = next(row for row in diag["audit"] if row["event"] == "step.committed")
    assert committed_audit["data"]["candidate_no"] == 1
    assert committed_audit["tool_call_id"] == action.tool_call_id
    assert diag["repair_candidates"][0]["tool_call_id"] == action.tool_call_id
    assert diag["repair_candidates"][0]["patch"] == patch.model_dump()
    with pytest.raises(LeaseLost):
        await store.finish(ex, {"business_outcome": "UNRESOLVED"})
    with pytest.raises(RuntimeFault, match="DELIVERY_NOT_VERIFIED"):
        await store.finish(
            recovered,
            {
                "business_outcome": "DELIVERED",
                "candidate_no": 1,
                "candidate_hash": tree_hash(files),
            },
        )
    await store.fail(recovered, "TEST_PROBE_DONE")


async def test_reserved_candidate_is_not_incremented_by_attempt():
    store, p, ex, action = await admitted()
    await store.prepare(ex, action)
    async with store.connection() as conn:
        await conn.execute(
            "UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s",
            (ex.task_id,),
        )
    recovered = await store.claim("replacement", 60, ex.task_id)
    assert recovered
    assert await store.pending_action(recovered, 0) == action
    await store.prepare(recovered, action)
    assert len((await store.diagnostics(ex.task_id))["repair_candidates"]) == 1
    await store.fail(recovered, "TEST_PROBE_DONE")


async def test_persistent_model_budget_never_resets_on_retry():
    store, p, ex, action = await admitted()
    await store.prepare(ex, action)
    await store.dispatch(ex, action)
    config = Settings(
        model_enabled=True,
        model_name="test-transport-only",
        model_budget_id=str(uuid4()),
        model_request_limit=1,
        model_token_limit=100000,
        model_task_request_limit=1,
    )
    await reserve_model_call(store, ex, action, config, 100)
    with pytest.raises(RuntimeFault, match="MODEL_BUDGET_EXHAUSTED"):
        await reserve_model_call(store, ex, action, config, 100)
    assert len((await store.diagnostics(ex.task_id))["model_calls"]) == 1
    await store.fail(ex, "TEST_PROBE_DONE")


async def test_cancelled_task_cannot_deliver():
    store, p, ex, action = await admitted()
    await store.cancel(ex.task_id, p.principal_id)
    with pytest.raises(RuntimeFault, match="CANCELLED"):
        await store.finish(ex, {"business_outcome": "DELIVERED"})
    await store.fail(ex, "CANCELLED")


async def test_original_document_request_hash_stays_compatible():
    store = Store(settings.database_url.get_secret_value())
    principal = Principal(principal_id="compat-" + str(uuid4()))
    request = TaskRequest(document_id="DOC-001", session_id=str(uuid4()))
    key = str(uuid4())
    task = await store.submit(principal, request, key)
    legacy = {
        "task_type": "document_review",
        "document_id": "DOC-001",
        "session_id": request.session_id,
        "budget_ms": 120000,
        "publish_mode": "none",
    }
    assert task["request_digest"] == digest(legacy)
    assert (await store.submit(principal, request, key))["task_id"] == task["task_id"]
    await store.cancel(task["task_id"], principal.principal_id)


async def test_cleanup_requires_real_task_attempt_fence_relationship():
    store, p, ex, _ = await admitted()
    assert await store.attempt_active(ex.task_id, ex.attempt_id, ex.fence) is True
    assert await store.attempt_active(ex.task_id, ex.attempt_id, ex.fence + 1) is None
    assert await store.attempt_active(str(uuid4()), ex.attempt_id, ex.fence) is None
    await store.fail(ex, "PROBE_DONE")
    assert await store.attempt_active(ex.task_id, ex.attempt_id, ex.fence) is False


async def test_verification_cannot_bind_another_candidate_or_generation_action():
    from runtime.repair.persistence import start_verification

    store, p, ex, action = await admitted()
    await store.prepare(ex, action)
    await store.dispatch(ex, action)
    with pytest.raises(RuntimeFault, match="VERIFICATION_ACTION_MISMATCH"):
        await start_verification(store, ex, action, 1, "0" * 64)
    assert (await store.diagnostics(ex.task_id))["repair_verifications"] == []
    await store.fail(ex, "PROBE_DONE")


async def test_delivery_rejects_wrong_patch_even_with_matching_claimed_hash():
    from runtime.repair.persistence import validate_delivery

    class Cursor:
        async def fetchone(self):
            return {"candidate_hash": "a" * 64, "candidate_no": 1,
                    "patch": {"edits": [{"path": "src/solution.py", "old": "x", "new": "y"}]},
                    "verification": {"claimed": "not enough to bypass patch validation"}}

    class Connection:
        async def execute(self, *args):
            return Cursor()

    with pytest.raises(RuntimeFault, match="DELIVERY_NOT_VERIFIED"):
        await validate_delivery(Connection(), {"task_id": str(uuid4())}, {
            "business_outcome": "DELIVERED", "candidate_hash": "a" * 64, "candidate_no": 1,
            "patch": {"edits": []}})


async def test_model_budget_reservation_requires_dispatch_barrier():
    store, p, ex, action = await admitted()
    await store.prepare(ex, action)
    config = Settings(model_enabled=True, model_name="test-no-network", model_budget_id=str(uuid4()),
                      model_request_limit=1, model_token_limit=100000)
    with pytest.raises(RuntimeFault, match="MODEL_BEFORE_DISPATCH"):
        await reserve_model_call(store, ex, action, config, 10)
    assert (await store.diagnostics(ex.task_id))["model_calls"] == []
    await store.fail(ex, "PROBE_DONE")
