import asyncio
from typing import Any

from psycopg.types.json import Jsonb

from core.contracts.planning import PatchPlanner
from core.contracts.repair import CaseManifest, Patch
from core.errors import RuntimeFault
from runtime.context.repair_context import model_context
from runtime.planning.adapters import FakePlanner, ResponsesPlanner
from runtime.repair.harness import verify
from runtime.repair.manifest import canonical, sha, tree_hash
from runtime.repair.persistence import reserve_model_call, start_verification
from runtime.repair.workspace import apply_patch


async def invoke_repair(store, backend, config, action, execution, arguments, max_seconds):
    ctx = execution.context
    manifest = CaseManifest.model_validate(ctx["manifest"])
    number = arguments["candidate_no"]
    base = ctx["base_files"]
    if action.tool in {"repair.baseline", "repair.verify"}:
        files = (
            base
            if number == 0
            else apply_patch(manifest, base, Patch.model_validate(ctx["candidate"]["patch"]))
        )
        identity = await start_verification(store, execution, action, number, tree_hash(files))
        pause = config.test_mode and config.test_barrier == "repair_running" and number == 1
        async def started(name):
            inspection = await backend.inspect_state(name)
            if inspection["status"] != "RUNNING":
                raise RuntimeFault("TEST_CONTAINER_NOT_RUNNING")
            await store.barrier("repair_running:" + execution.task_id, execution)
        return await verify(
            backend,
            manifest,
            files,
            identity,
            attempt_id=execution.attempt_id,
            fence=execution.fence,
            remaining_seconds=max_seconds,
            test_pause=pause,
            on_test_started=started if pause else None,
        )
    if action.tool == "repair.generate":
        baseline = ctx["baseline"]["verdict"]
        if baseline["status"] != "FAIL" or set(baseline["failed_ids"]) != set(
            manifest.baseline_fail
        ):
            raise RuntimeFault("BASELINE_NOT_CONFIRMED")
        context = await asyncio.to_thread(model_context,
            execution, include_map=execution.request.repair_strategy != "single"
        )
        planner: PatchPlanner
        call_id = None
        if execution.request.planner_mode == "real":
            call_id = await reserve_model_call(
                store, execution, action, config, len(canonical(context))
            )
            planner = ResponsesPlanner(config)
        else:
            planner = FakePlanner()
        metadata: dict[str, Any] = {"status": "UNKNOWN"}
        try:
            async with asyncio.timeout(max_seconds):
                patch, metadata = await planner.propose(context, max_seconds=max_seconds)
            files = apply_patch(manifest, base, patch)
        except (ValueError, RuntimeFault) as exc:
            metadata = {"status": "ERROR", "code": getattr(exc, "code", type(exc).__name__)}
            raise RuntimeFault("REPAIR_GENERATION_FAILED", metadata["code"]) from None
        finally:
            if call_id:
                metadata.update(input_context=context, context_sha256=sha(canonical(context)),
                                context_bytes=len(canonical(context)))
                async with store.connection() as conn:
                    await store._locked(conn, execution)
                    await conn.execute(
                        "UPDATE arc_ref.model_calls SET result=%s WHERE call_id=%s",
                        (Jsonb(metadata), call_id),
                    )
        return {
            "candidate_no": number,
            "patch": patch.model_dump(),
            "candidate_hash": tree_hash(files),
            "generation": metadata,
        }
    checked = ctx["verification"]["verdict"]
    return {
        "business_outcome": "DELIVERED" if checked["status"] == "PASS" else "UNRESOLVED",
        "gate_verdict": checked["status"],
        "reason_code": checked["reason_code"],
        "candidate_no": number,
        "candidate_hash": ctx["candidate"]["candidate_hash"],
        "patch": ctx["candidate"]["patch"],
        "planner_mode": execution.request.planner_mode,
    }
