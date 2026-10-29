"""Repair hooks run inside Store's existing fenced transaction, never a second commit."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from core.contracts.repair import CaseManifest, Patch, RunRecord, VerificationIdentity
from core.errors import RuntimeFault
from runtime.repair.manifest import canonical, sha, tree_hash
from runtime.repair.verdict import judge
from runtime.repair.workspace import apply_patch


async def reserve_candidate(conn, task, action):
    ctx = task["context"]
    manifest = CaseManifest.model_validate(ctx["manifest"])
    baseline = ctx.get("baseline", {}).get("verdict", {})
    if baseline.get("status") != "FAIL" or set(baseline.get("failed_ids", [])) != set(
        manifest.baseline_fail
    ):
        raise RuntimeFault("BASELINE_NOT_CONFIRMED")
    n = action.arguments["candidate_no"]
    count = await (
        await conn.execute(
            "SELECT count(*) AS n FROM arc_ref.repair_candidates WHERE task_id=%s",
            (task["task_id"],),
        )
    ).fetchone()
    maximum = manifest.limits.candidates if task["request"]["repair_strategy"] == "loop" else 1
    if not count or n != count["n"] + 1 or n > maximum:
        raise RuntimeFault("CANDIDATE_BUDGET_EXCEEDED")
    if n > 1:
        previous = await (
            await conn.execute(
                "SELECT verification FROM arc_ref.repair_candidates WHERE task_id=%s AND candidate_no=%s",
                (task["task_id"], n - 1),
            )
        ).fetchone()
        if (
            not previous
            or not previous["verification"]
            or previous["verification"]["verdict"]["status"] != "FAIL"
        ):
            raise RuntimeFault("PREVIOUS_CANDIDATE_NOT_FAILED")
    await conn.execute(
        "INSERT INTO arc_ref.repair_candidates(task_id,candidate_no,tool_call_id) VALUES(%s,%s,%s)",
        (task["task_id"], n, action.tool_call_id),
    )


async def commit_repair(conn, task, action, observation):
    manifest = CaseManifest.model_validate(task["context"]["manifest"])
    if action.tool == "repair.generate":
        n = action.arguments["candidate_no"]
        if observation["candidate_no"] != n:
            raise RuntimeFault("CANDIDATE_ID_MISMATCH")
        patch = Patch.model_validate(observation["patch"])
        files = apply_patch(manifest, task["context"]["base_files"], patch)
        if tree_hash(files) != observation["candidate_hash"]:
            raise RuntimeFault("CANDIDATE_HASH_MISMATCH")
        cursor = await conn.execute(
            "UPDATE arc_ref.repair_candidates SET patch=%s,candidate_hash=%s WHERE task_id=%s AND candidate_no=%s AND tool_call_id=%s AND patch IS NULL",
            (Jsonb(patch.model_dump()), tree_hash(files), task["task_id"], n, action.tool_call_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeFault("CANDIDATE_ALREADY_COMMITTED")
    elif action.tool in {"repair.baseline", "repair.verify"}:
        record = RunRecord.model_validate(observation["record"])
        row = await (
            await conn.execute(
                "SELECT * FROM arc_ref.repair_verifications WHERE verification_run_id=%s AND task_id=%s AND tool_call_id=%s FOR UPDATE",
                (record.identity.verification_run_id, task["task_id"], action.tool_call_id),
            )
        ).fetchone()
        if (
            not row
            or row["identity"] != record.identity.model_dump()
            or str(row["attempt_id"]) != str(task["current_attempt"])
            or row["result"] is not None
        ):
            raise RuntimeFault("VERIFICATION_IDENTITY_MISMATCH")
        raw = observation["raw_report"]
        verdict = judge(manifest, record, raw.encode() if raw is not None else None)
        if verdict.model_dump() != observation["verdict"]:
            raise RuntimeFault("VERDICT_FORGED")
        await conn.execute(
            "UPDATE arc_ref.repair_verifications SET result=%s,finished_at=clock_timestamp() WHERE verification_run_id=%s",
            (Jsonb(observation), record.identity.verification_run_id),
        )
        if action.tool == "repair.verify":
            cursor = await conn.execute(
                "UPDATE arc_ref.repair_candidates SET verification=%s WHERE task_id=%s AND candidate_no=%s AND candidate_hash=%s",
                (
                    Jsonb(observation),
                    task["task_id"],
                    record.identity.candidate_no,
                    record.identity.candidate_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeFault("CANDIDATE_HASH_MISMATCH")
    elif action.tool == "repair.report":
        await validate_delivery(conn, task, observation)


async def validate_delivery(conn, task, result):
    if result.get("business_outcome") not in {"DELIVERED", "UNRESOLVED", "REJECTED_POLICY"}:
        raise RuntimeFault("INVALID_REPAIR_OUTCOME")
    if result["business_outcome"] == "DELIVERED":
        row = await (
            await conn.execute(
                "SELECT * FROM arc_ref.repair_candidates WHERE task_id=%s ORDER BY candidate_no DESC LIMIT 1",
                (task["task_id"],),
            )
        ).fetchone()
        if (
            not row
            or not row["verification"]
            or result.get("candidate_hash") != row["candidate_hash"]
            or result.get("candidate_no") != row["candidate_no"]
            or result.get("patch") != row["patch"]
        ):
            raise RuntimeFault("DELIVERY_NOT_VERIFIED")
        checked = row["verification"]
        verdict = judge(
            CaseManifest.model_validate(task["context"]["manifest"]),
            RunRecord.model_validate(checked["record"]),
            checked["raw_report"].encode() if checked["raw_report"] is not None else None,
        )
        if verdict.status != "PASS" or verdict.identity.candidate_hash != row["candidate_hash"]:
            raise RuntimeFault("DELIVERY_NOT_VERIFIED")


async def start_verification(store, execution, action, candidate_no, candidate_hash):
    manifest = CaseManifest.model_validate(execution.context["manifest"])
    identity = VerificationIdentity(
        task_id=execution.task_id,
        candidate_no=candidate_no,
        candidate_hash=candidate_hash,
        manifest_hash=sha(canonical(manifest.model_dump())),
        verification_run_id=str(uuid4()),
        nonce=uuid4().hex,
        image_digest=manifest.image_digest,
        test_suite_hash=manifest.test_suite_hash,
        repo_content_hash=manifest.repo_content_hash,
        runner_hash=manifest.runner_hash,
    )
    async with store.connection() as conn:
        task = await store._locked(conn, execution)
        saved = await store._persisted_action(conn, execution, action)
        if saved["state"] != "DISPATCHED":
            raise RuntimeFault("VERIFICATION_BEFORE_DISPATCH")
        if action.tool not in {"repair.baseline", "repair.verify"} or action.arguments["candidate_no"] != candidate_no:
            raise RuntimeFault("VERIFICATION_ACTION_MISMATCH")
        expected_hash: str | None
        if candidate_no == 0:
            expected_hash = manifest.repo_content_hash
        else:
            row = await (await conn.execute("SELECT candidate_hash FROM arc_ref.repair_candidates WHERE task_id=%s AND candidate_no=%s AND patch IS NOT NULL", (task["task_id"], candidate_no))).fetchone()
            expected_hash = row["candidate_hash"] if row else None
        if candidate_hash != expected_hash:
            raise RuntimeFault("CANDIDATE_HASH_MISMATCH")
        await conn.execute(
            "INSERT INTO arc_ref.repair_verifications(verification_run_id,task_id,attempt_id,tool_call_id,candidate_no,identity) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                identity.verification_run_id,
                execution.task_id,
                execution.attempt_id,
                action.tool_call_id,
                candidate_no,
                Jsonb(identity.model_dump()),
            ),
        )
    return identity


async def reserve_model_call(store, execution, action, config, input_bytes):
    if (
        not config.model_enabled
        or not config.model_name
        or not config.model_budget_id
        or config.model_request_limit <= 0
        or config.model_token_limit <= 0
    ):
        raise RuntimeFault("MODEL_BLOCKED")
    reservation = input_bytes + 4096 + config.model_max_output_tokens
    limits = {
        "model": config.model_name,
        "temperature": config.model_temperature,
        "requests": config.model_request_limit,
        "tokens": config.model_token_limit,
        "per_task": config.model_task_request_limit,
        "output": config.model_max_output_tokens,
    }
    async with store.connection() as conn:
        await store._locked(conn, execution)
        saved = await store._persisted_action(conn, execution, action)
        if action.tool != "repair.generate" or saved["state"] != "DISPATCHED":
            raise RuntimeFault("MODEL_BEFORE_DISPATCH")
        await conn.execute(
            "INSERT INTO arc_ref.model_budgets(budget_id,config) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (config.model_budget_id, Jsonb(limits)),
        )
        budget = await store._one(
            conn,
            "SELECT * FROM arc_ref.model_budgets WHERE budget_id=%s FOR UPDATE",
            (config.model_budget_id,),
        )
        count = await store._one(
            conn,
            "SELECT count(*) AS n FROM arc_ref.model_calls WHERE task_id=%s",
            (execution.task_id,),
        )
        if (
            budget["config"] != limits
            or count["n"] >= config.model_task_request_limit
            or budget["used_requests"] >= config.model_request_limit
            or budget["reserved_tokens"] + reservation > config.model_token_limit
        ):
            raise RuntimeFault("MODEL_BUDGET_EXHAUSTED")
        call_id = str(uuid4())
        await conn.execute(
            "UPDATE arc_ref.model_budgets SET used_requests=used_requests+1,reserved_tokens=reserved_tokens+%s WHERE budget_id=%s",
            (reservation, config.model_budget_id),
        )
        await conn.execute(
            "INSERT INTO arc_ref.model_calls(call_id,budget_id,task_id,attempt_id,tool_call_id,reserved_tokens) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                call_id,
                config.model_budget_id,
                execution.task_id,
                execution.attempt_id,
                action.tool_call_id,
                reservation,
            ),
        )
        await store._audit(
            conn,
            execution.principal.principal_id,
            "model.reserved",
            execution.task_id,
            execution.attempt_id,
            action.tool_call_id,
            {"call_id": call_id, "reserved_tokens": reservation},
        )
    return call_id
