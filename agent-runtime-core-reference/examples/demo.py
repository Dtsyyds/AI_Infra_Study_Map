"""A–G确定性场景入口。BLOCKED单列，局部探针不冒充完整业务审核。"""
import argparse
import asyncio
import copy
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.task import TaskRequest
from core.errors import RuntimeFault
from core.nodes import DeterministicPlanner
from runtime.context.engine import ContextEngine
from runtime.security.policy import principal_from_token
from runtime.security.redaction import redact
from runtime.storage.store import Store, digest
from runtime.tools.docker_backend import DockerBackend
from runtime.tools.invoker import ToolRuntime

STORE = Store(settings.database_url.get_secret_value())
PRINCIPAL = principal_from_token(settings.user_token.get_secret_value())


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://127.0.0.1:18081", trust_env=False, timeout=15,
                             headers={"Authorization": "Bearer " + settings.user_token.get_secret_value()})


async def worker(task_id: str, directory: Path, *, wait_seconds: float = 45) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(ARC_TEST_MODE="true", ARC_LEASE_SECONDS="3")
    with (directory / f"worker-{task_id}.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "runtime.orchestrator.worker",
                                                       "--once", "--task-id", task_id,
                                                       env=env, stdout=log, stderr=log, start_new_session=True)
        try:
            await asyncio.wait_for(process.wait(), wait_seconds)
            assert process.returncode == 0
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()


async def normal(directory: Path) -> dict[str, Any]:
    tasks = []
    async with client() as http:
        for doc, expected in [("DOC-001", "NEEDS_DOCUMENTS"), ("DOC-002", "PASS")]:
            response = await http.post("/tasks", headers={"Idempotency-Key": str(uuid4())},
                                       json={"document_id": doc, "session_id": str(uuid4()), "budget_ms": 120000})
            assert response.status_code == 202, response.text
            task_id = response.json()["task_id"]
            await worker(task_id, directory)
            diag = (await http.get(f"/tasks/{task_id}/diagnostics")).json()
            (directory / f"diagnostics-{task_id}.json").write_text(json.dumps(diag, ensure_ascii=False, indent=2))
            task = diag["task"]
            if task["error_code"] == "DOCKER_UNAVAILABLE":
                status = "blocked"
                assert task["status"] == "FAILED" and task["checkpoint"] == 0
            else:
                assert task["status"] == "SUCCEEDED", task
                assert task["result"]["review_decision"] == expected
                assert len(diag["artifacts"]) == 1
                status = "passed"
            tasks.append({"task_id": task_id, "document_id": doc, "expected_decision": expected,
                          "runtime_status": task["status"], "checkpoint": task["checkpoint"],
                          "result": task["result"], "error_code": task["error_code"], "status": status,
                          "retrieved_rules": task["context"].get("rules"), "ledger": diag["ledger"]})
    return {"status": "blocked" if any(t["status"] == "blocked" for t in tasks) else "passed",
            "tasks": tasks, "reason": "真实Docker不可用时不会以宿主执行冒充容器检查"}


async def duplicate() -> dict[str, Any]:
    async with client() as http:
        body = {"document_id": "DOC-001", "session_id": str(uuid4())}
        key = str(uuid4())
        one = await http.post("/tasks", json=body, headers={"Idempotency-Key": key})
        two = await http.post("/tasks", json=body, headers={"Idempotency-Key": key})
        conflict = await http.post("/tasks", json={**body, "document_id": "DOC-002"}, headers={"Idempotency-Key": key})
        assert one.status_code == two.status_code == 202
        task_id = one.json()["task_id"]
        assert task_id == two.json()["task_id"] and conflict.status_code == 409
        async with STORE.connection() as conn:
            count = await (await conn.execute("SELECT count(*) AS n FROM arc_ref.tasks WHERE principal_id=%s AND session_id=%s", (PRINCIPAL.principal_id, body["session_id"]))).fetchone()
        assert count and count["n"] == 1
        await http.post(f"/tasks/{task_id}/cancel")
    return {"status": "passed", "task_id": task_id, "same_task": True, "conflict_http": 409, "logical_tasks": 1}


async def sse_disconnect() -> dict[str, Any]:
    async with client() as http:
        response = await http.post("/tasks", json={"document_id": "DOC-001", "session_id": str(uuid4())}, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        async with http.stream("GET", f"/tasks/{task_id}/events") as stream:
            lines = stream.aiter_lines()
            seen = []
            while True:
                line = await anext(lines)
                seen.append(line)
                if line == "":
                    break
            assert any(line.startswith("data:") for line in seen)
            try:
                await asyncio.wait_for(anext(lines), 1.15)
                raise AssertionError("unchanged persistent version must not emit progress")
            except TimeoutError:
                pass
        snapshot = (await http.get(f"/tasks/{task_id}")).json()
        assert snapshot["status"] == "QUEUED" and not snapshot["cancel_requested"]
        invalid = await http.get(f"/tasks/{task_id}/events", headers={"Last-Event-ID": str(snapshot["version"]+100)})
        assert invalid.status_code == 400
        await http.post(f"/tasks/{task_id}/cancel")
    return {"status": "passed", "task_id": task_id, "disconnect_did_not_cancel": True, "unchanged_no_progress": True, "future_event_http": 400}


async def security(directory: Path) -> dict[str, Any]:
    async with client() as http:
        response = await http.post("/tasks", headers={"Authorization": "Bearer "+settings.restricted_token.get_secret_value(), "Idempotency-Key": str(uuid4())},
                                   json={"document_id": "DOC-001", "session_id": str(uuid4())})
    assert response.status_code == 202
    denied_id = response.json()["task_id"]
    await worker(denied_id, directory)
    denied = await STORE.diagnostics(denied_id)
    assert denied["task"]["error_code"] == "TOOL_FORBIDDEN"
    assert sum(row["dispatch_count"] for row in denied["ledger"]) == 0
    task = await STORE.submit(PRINCIPAL, TaskRequest(document_id="DOC-001", session_id=str(uuid4())), str(uuid4()))
    execution = await STORE.claim("arc-ref-audit-probe", 30, task["task_id"])
    assert execution
    arguments = {"mode": "unsafe", "report": {"document_id": "DOC-001", "document_version": "1", "review_decision": "PASS", "missing_documents": [], "citations": [], "fictional": True}}
    action = PreparedAction(task_id=execution.task_id, step_no=0, tool_call_id=str(uuid4()), tool="report.publish", arguments=arguments, arguments_digest=digest(arguments), effect_class=EffectClass.EXTERNAL_UNSAFE)
    await STORE.prepare(execution, action)
    trigger = "demo_audit_"+uuid4().hex
    from psycopg import sql
    async with STORE.connection() as conn:
        await conn.execute(sql.SQL("CREATE FUNCTION arc_ref.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.tool_call_id={}::uuid THEN RAISE EXCEPTION 'intentional demo audit failure'; END IF; RETURN NEW; END $$").format(sql.Identifier(trigger), sql.Literal(action.tool_call_id)))
        await conn.execute(sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON arc_ref.audit FOR EACH ROW EXECUTE FUNCTION arc_ref.{}()").format(sql.Identifier(trigger), sql.Identifier(trigger)))
    tools = ToolRuntime(STORE, ContextEngine(STORE), settings)
    try:
        try:
            await tools.invoke(action, execution)
            raise AssertionError("audit failure must reject")
        except psycopg.errors.RaiseException:
            pass
    finally:
        await tools.stop()
        async with STORE.connection() as conn:
            await conn.execute(sql.SQL("DROP TRIGGER {} ON arc_ref.audit").format(sql.Identifier(trigger)))
            await conn.execute(sql.SQL("DROP FUNCTION arc_ref.{}()").format(sql.Identifier(trigger)))
    async with STORE.connection() as conn:
        counts = await (await conn.execute("SELECT count(*) AS n FROM arc_business.requests WHERE idempotency_key=%s", (action.tool_call_id,))).fetchone()
    ledger = await STORE.ledger(action.tool_call_id)
    assert counts and counts["n"] == ledger["dispatch_count"] == 0
    await STORE.fail(execution, "AUDIT_PROBE_EXPECTED_REJECTION")
    # 真正经过 MCP → Worker → Checkpoint 的模拟秘密，不只测试替换函数。
    marker = "SIMULATED_SECRET_MUST_NOT_LEAK"
    async with STORE.connection() as conn:
        await conn.execute("INSERT INTO arc_ref.documents(document_id,version,data) VALUES ('DOC-994',%s,%s)",
                           (str(uuid4()), Jsonb({"amount": 8000, "attachments": ["invoice"],
                                               "source": "fictional://security/DOC-994", "secret": marker})))
    try:
        secret_task = await STORE.submit(PRINCIPAL, TaskRequest(document_id="DOC-994", session_id=str(uuid4())), str(uuid4()))
        await worker(secret_task["task_id"], directory)
        secret_diag = await STORE.diagnostics(secret_task["task_id"])
        assert len(secret_diag["steps"]) >= 1
        assert marker not in json.dumps(secret_diag)
        trace = await asyncio.to_thread((directory / f"worker-{secret_task['task_id']}.log").read_text)
        assert marker not in trace
    finally:
        async with STORE.connection() as conn:
            await conn.execute("DELETE FROM arc_ref.documents WHERE document_id='DOC-994'")
    available = await DockerBackend().availability()
    return {"status": "blocked" if not available["available"] else "passed",
            "passed_checks": ["unauthorized_dispatch_zero", "audit_failure_external_requests_zero", "actual_mcp_secret_absent_from_diagnostics_audit_trace"],
            "denied_task_id": denied_id, "audit_task_id": task["task_id"], "external_requests": counts["n"],
            "docker_timeout": "blocked" if not available["available"] else "see docker test report",
            "secret_task_id": secret_task["task_id"], "secret_protection": "passed"}


async def cancellation_order() -> dict[str, Any]:
    """仅测试终结事务的两个提交顺序，结果标记为probe，不冒充业务审核。"""
    outcomes = []
    for cancel_first in [True, False]:
        task = await STORE.submit(PRINCIPAL, TaskRequest(document_id="DOC-001", session_id=str(uuid4())), str(uuid4()))
        execution = await STORE.claim("arc-ref-cancel-order-probe", 30, task["task_id"])
        assert execution
        if cancel_first:
            await STORE.cancel(execution.task_id, PRINCIPAL.principal_id)
            try:
                await STORE.finish(execution, {"probe_only": "cancel-success-order"})
                raise AssertionError("success after cancellation commit")
            except RuntimeFault as exc:
                assert exc.code == "CANCELLED"
            await STORE.fail(execution, "CANCELLED")
        else:
            await STORE.finish(execution, {"probe_only": "success-cancel-order"})
            await STORE.cancel(execution.task_id, PRINCIPAL.principal_id)
        snapshot = await STORE.snapshot(execution.task_id)
        assert snapshot["status"] == ("CANCELLED" if cancel_first else "SUCCEEDED")
        outcomes.append({"task_id": execution.task_id, "cancel_first": cancel_first, "status": snapshot["status"]})
    return {"status": "passed", "scope": "controlled finalization transaction ordering probe", "tasks": outcomes}


async def context_consistency() -> dict[str, Any]:
    task = await STORE.submit(PRINCIPAL, TaskRequest(document_id="DOC-003", session_id=str(uuid4())), str(uuid4()))
    execution = await STORE.claim("arc-ref-context-probe", 30, task["task_id"])
    assert execution
    engine = ContextEngine(STORE)
    execution.context = (await engine.build(execution)).model_dump()
    action = DeterministicPlanner().plan(execution, 0)
    assert action
    await STORE.prepare(execution, action)
    tools = ToolRuntime(STORE, engine, settings)
    observation = await tools.invoke(action, execution)
    context = engine.observe(execution.context, {"document": observation})
    for index in range(60):
        context = engine.observe(context, {"history_note": {"index": index, "text": "虚构历史。"*200}})
    assert context["summary"] and len(context["working_memory"]) <= 4
    bad = copy.deepcopy(context)
    bad["protected_facts"].pop("document")
    try:
        engine.apply_summary(context, bad)
        raise AssertionError("bad summary accepted")
    except RuntimeFault as exc:
        assert exc.code == "PROTECTED_FACTS_LOST"
    await STORE.checkpoint(execution, action, observation, context)
    async with STORE.connection() as conn:
        await conn.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (execution.task_id,))
    rebuilt_execution = await STORE.claim("arc-ref-context-rebuild", 30, execution.task_id)
    assert rebuilt_execution
    rebuilt = await engine.build(rebuilt_execution)
    assert rebuilt.protected_facts == context["protected_facts"]
    assert rebuilt_execution.attempt_id != execution.attempt_id
    await STORE.cancel(execution.task_id, PRINCIPAL.principal_id)
    await STORE.fail(rebuilt_execution, "CANCELLED")
    await tools.stop()
    return {"status": "passed", "task_id": execution.task_id, "attempt_ids": [execution.attempt_id, rebuilt_execution.attempt_id],
            "history_entries": 60, "retained_entries": len(context["working_memory"]), "summary": context["summary"],
            "protected_facts_preserved": True, "bad_summary_rejected": True, "token_counter": "estimated only"}


async def run(directory: Path) -> dict[str, Any]:
    from examples.process_scenarios import crash_checkpoint, external_effect, lease_competition
    results: dict[str, Any] = {"reference_only": True, "formal_W8_acceptance": False,
                               "not_run": ["270次压测", "全部10/30次重复实验"]}
    scenarios = [("A", lambda: normal(directory)), ("B", duplicate),
                 ("C", crash_checkpoint), ("D_idempotent", lambda: external_effect("idempotent")),
                 ("D_unsafe", lambda: external_effect("unsafe")), ("D_queryable", lambda: external_effect("queryable")),
                 ("E_leases", lease_competition), ("E_cancellation", cancellation_order),
                 ("E_sse", sse_disconnect), ("F", lambda: security(directory)), ("G", context_consistency)]
    for name, fn in scenarios:
        tick = time.monotonic()
        try:
            result = await fn()
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__, "error": redact(str(exc))}
        result["elapsed_seconds"] = round(time.monotonic()-tick, 3)
        results[name] = result
        (directory / "demo-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(name, result["status"], flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="20261022-reference")
    args = parser.parse_args()
    directory = Path("reports") / args.run
    directory.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(run(directory))
    raise SystemExit(1 if any(isinstance(v, dict) and v.get("status") == "failed" for v in results.values()) else 0)


if __name__ == "__main__":
    main()
