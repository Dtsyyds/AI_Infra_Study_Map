"""Controlled local effect-recovery ablation; no candidate execution or model requests."""

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.task import TaskRequest
from examples.experiment_driver import append_record
from examples.load import wait_for_fresh_admission_window
from examples.process_scenarios import (
    REVIEWER,
    _barrier,
    _cleanup_task,
    _kill,
    _spawn,
    _wait_exit,
    external_effect,
)
from runtime.repair.manifest import canonical, sha
from runtime.storage.store import Store
from runtime.tools.registry import Report

ARMS = ("volatile_key", "stable_key_only", "runtime")
MODES = ("idempotent", "unsafe")


def slots(repeats):
    if not 1 <= repeats <= 10:
        raise ValueError("repeats must be in 1..10")
    controls = [{"arm": arm, "mode": mode, "crash": False, "repeat": 0}
                for mode in MODES for arm in ARMS]
    faults = [{"arm": arm, "mode": mode, "crash": True, "repeat": repeat}
              for repeat in range(1, repeats + 1) for mode in MODES
              for arm in ARMS[repeat % 3:] + ARMS[:repeat % 3]]
    return controls + faults


def summarize(rows, repeats):
    identity = lambda r: (r["arm"], r["mode"], r["crash"], r["repeat"])  # noqa: E731
    if len(rows) != len(slots(repeats)) or {identity(r) for r in rows} != {identity(s) for s in slots(repeats)}:
        raise ValueError("INCOMPLETE_OR_DUPLICATE_SLOTS")
    grouped = defaultdict(list)
    for row in rows:
        if row["effects"] < 1 or row["requests"] < row["effects"]:
            raise ValueError("INVALID_EXTERNAL_COUNTS")
        grouped[(row["arm"], row["mode"], row["crash"])].append(row)
    return {
        "status": "COMPLETE_RECORDS", "trials": len(rows), "model_calls": 0,
        "groups": [{"arm": arm, "mode": mode, "crash": crash, "n": len(items),
                    "requests": sum(r["requests"] for r in items),
                    "effects": sum(r["effects"] for r in items),
                    "duplicate_effects": sum(r["effects"] - 1 for r in items),
                    "automatic_successes": sum(r["automatic_status"] in {"SUCCEEDED", "REPORTED_SUCCESS"} for r in items),
                    "needs_review": sum(r["automatic_status"] == "NEEDS_REVIEW" for r in items),
                    "operator_confirmations": sum(r["operator_confirmation"] for r in items)}
                   for (arm, mode, crash), items in sorted(grouped.items())],
        "limits": "constructed local publish probe; fixed committed-effect barrier; no production reliability, repair rate, framework ranking or latency comparison",
    }


def report_payload():
    return Report(document_id="DOC-002", document_version="1", review_decision="PASS",
                  missing_documents=[], citations=[]).model_dump(mode="json")


async def external_facts(store, task_id):
    async with store.connection() as conn:
        requests = await (await conn.execute(
            "SELECT idempotency_key,mode FROM arc_business.requests WHERE task_id=%s ORDER BY request_id", (task_id,))).fetchall()
        effects = await (await conn.execute(
            "SELECT effect_id,idempotency_key,mode,report FROM arc_business.effects WHERE task_id=%s ORDER BY effect_id", (task_id,))).fetchall()
    return {"requests": [{**r} for r in requests],
            "effects": [{**r, "effect_id": str(r["effect_id"])} for r in effects]}


async def direct_trial(store, directory, slot):
    task = await store.submit(REVIEWER, TaskRequest(document_id="DOC-002", session_id=str(uuid4()), budget_ms=120000), str(uuid4()))
    task_id, first_key = task["task_id"], str(uuid4())
    request = directory / "request.json"
    request.write_text(json.dumps({"task_id": task_id, "mode": slot["mode"], "report": report_payload()}))
    key_file = directory / "stable-key.json"
    if slot["arm"] == "stable_key_only":
        with key_file.open("x") as stream:
            json.dump({"tool_call_id": first_key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    processes, logs = [], []
    keys = [first_key]
    before = None
    try:
        if slot["crash"]:
            async with store.connection() as conn:
                await conn.execute("INSERT INTO arc_business.controls(name,value) VALUES(%s,%s)", (first_key, Jsonb({"pause": True})))
        for attempt in range(2 if slot["crash"] else 1):
            key = first_key if attempt == 0 or slot["arm"] == "stable_key_only" else str(uuid4())
            if attempt:
                keys.append(key)
            args = ["--key-file", str(key_file)] if slot["arm"] == "stable_key_only" else ["--key", key]
            process, log = await _spawn("tests.fakes.comparison_publish", "--request", str(request), *args,
                                         ARC_MODEL_ENABLED="false", ARC_MODEL_REQUEST_LIMIT="0", ARC_MODEL_TOKEN_LIMIT="0")
            processes.append(process)
            logs.append(log)
            if slot["crash"] and attempt == 0:
                await _barrier(store, "business:" + first_key, process)
                before = await external_facts(store, task_id)
                assert len(before["requests"]) == len(before["effects"]) == 1
                await _kill(process)
                assert process.returncode == -9
                async with store.connection() as conn:
                    await conn.execute("UPDATE arc_business.controls SET value=%s WHERE name=%s", (Jsonb({"pause": False}), first_key))
            else:
                await _wait_exit(process, log)
        facts = await external_facts(store, task_id)
        return {"task_id": task_id, "tool_call_ids": keys, "logs": logs,
                "before_crash": before, "external_facts": facts,
                "requests": len(facts["requests"]), "effects": len(facts["effects"]),
                "automatic_status": "REPORTED_SUCCESS", "operator_confirmation": False,
                "scope": "Task is only harness identity; direct arms do not use Runtime ledger or recovery"}
    finally:
        for process in processes:
            await _kill(process)
        async with store.connection() as conn:
            await conn.execute("UPDATE arc_business.controls SET value=%s WHERE name=%s", (Jsonb({"pause": False}), first_key))
        await _cleanup_task(store, task_id)


async def runtime_control(store, mode):
    task = await store.submit(REVIEWER, TaskRequest(document_id="DOC-002", session_id=str(uuid4()), budget_ms=120000), str(uuid4()))
    task_id, key = task["task_id"], str(uuid4())
    processes = []
    try:
        fixture = await store.claim("comparison-fixture", 3, task_id)
        assert fixture
        arguments = {"report": report_payload(), "mode": mode}
        action = PreparedAction(task_id=task_id, step_no=0, tool_call_id=key, tool="report.publish",
                                arguments=arguments, arguments_digest=sha(canonical(arguments)),
                                effect_class=EffectClass.EXTERNAL_IDEMPOTENT if mode == "idempotent" else EffectClass.EXTERNAL_UNSAFE)
        await store.prepare(fixture, action)
        async with store.connection() as conn:
            await conn.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task_id,))
        process, log = await _spawn("tests.fakes.publish_probe", task_id,
                                     ARC_MODEL_ENABLED="false", ARC_MODEL_REQUEST_LIMIT="0", ARC_MODEL_TOKEN_LIMIT="0")
        processes.append(process)
        await _wait_exit(process, log)
        diag = await store.diagnostics(task_id)
        facts = await external_facts(store, task_id)
        return {"task_id": task_id, "tool_call_ids": [key], "logs": [log], "diagnostics": diag,
                "external_facts": facts, "requests": len(facts["requests"]), "effects": len(facts["effects"]),
                "automatic_status": diag["task"]["status"], "operator_confirmation": False}
    finally:
        for process in processes:
            await _kill(process)
        await _cleanup_task(store, task_id)


async def run(repeats=5):
    if settings.model_enabled or settings.model_request_limit or settings.model_token_limit:
        raise RuntimeError("comparison requires models disabled and budgets zero")
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "reliability-comparison"
    out.mkdir(parents=True, exist_ok=False)
    plan = {"slots": slots(repeats), "repeats": repeats, "arms": ARMS, "modes": MODES,
            "barrier": "external transaction committed, HTTP result not received, then SIGKILL",
            "controls": "one no-crash trial per arm/mode", "request_report": report_payload(),
            "stable_key_control": "fsync key file, no ledger or uncertainty-aware replay policy",
            "runtime_unsafe": "NEEDS_REVIEW before explicit test-operator confirmation; never count that as automatic success",
            "failure_policy": "retain failed/interrupted records; no overwriting or silent reruns",
            "hypothesis": "stable key suffices only when dependency deduplicates; unsafe effects require uncertainty-aware recovery",
            "model_calls": 0}
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    store = Store(settings.database_url.get_secret_value())
    async with store.connection() as conn:
        initial_calls = await (await conn.execute("SELECT count(*) AS n FROM arc_ref.model_calls")).fetchone()
    rows = []
    for index, slot in enumerate(plan["slots"]):
        directory = out / f"trial-{index:02}"
        directory.mkdir()
        # Respect the existing real admission window; do not reset the quota.
        async with store.connection() as conn:
            admission = await (await conn.execute("SELECT requests FROM arc_ref.admissions WHERE principal_id='demo-reviewer'")).fetchone()
        delay = await wait_for_fresh_admission_window() if admission and admission["requests"] >= 30 else 0
        append_record(out / "events.jsonl", {"event": "TRIAL_STARTED", "slot": slot, "index": index, "window_wait_seconds": delay})
        try:
            if slot["arm"] != "runtime":
                result = await direct_trial(store, directory, slot)
            elif not slot["crash"]:
                result = await runtime_control(store, slot["mode"])
            else:
                probe = await external_effect(slot["mode"])
                facts = await external_facts(store, probe["task_id"])
                result = {"task_id": probe["task_id"], "probe": probe, "logs": probe["logs"],
                          "external_facts": facts, "requests": len(facts["requests"]), "effects": len(facts["effects"]),
                          "automatic_status": probe["recovery_task_status"], "operator_confirmation": bool(probe["reconciliation"])}
            row = {**slot, **result}
            append_record(out / "raw.jsonl", row)
            rows.append(row)
            append_record(out / "events.jsonl", {"event": "TRIAL_FINISHED", "slot": slot, "index": index})
            print(json.dumps({**slot, "requests": row["requests"], "effects": row["effects"], "automatic_status": row["automatic_status"]}), flush=True)
        except BaseException as exc:
            append_record(out / "events.jsonl", {"event": "TRIAL_INTERRUPTED", "slot": slot, "index": index, "error_type": type(exc).__name__})
            raise
    summary = summarize(rows, repeats)
    async with store.connection() as conn:
        final_calls = await (await conn.execute("SELECT count(*) AS n FROM arc_ref.model_calls")).fetchone()
    assert initial_calls == final_calls
    summary["new_model_reservations"] = 0
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    asyncio.run(run(parser.parse_args().repeats))
