"""真实 PostgreSQL 与 stdio 的工具边界；外部审计拒绝使用独立业务服务。"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.errors import RuntimeFault
from runtime.context.engine import ContextEngine
from runtime.security import principal_from_token
from runtime.storage.store import Store
from runtime.tools.invoker import ToolRuntime
from runtime.tools.mcp_gateway import MCPDocumentGateway

pytestmark = pytest.mark.integration


def prepared(execution, tool, arguments, effect):
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return PreparedAction(
        task_id=execution.task_id, step_no=0, tool_call_id=str(uuid4()), tool=tool,
        arguments=arguments, arguments_digest=hashlib.sha256(encoded.encode()).hexdigest(),
        effect_class=effect,
    )


async def admitted(store):
    principal = principal_from_token(settings.user_token.get_secret_value())
    task = await store.submit(principal, TaskRequest(document_id="DOC-902", session_id=str(uuid4())), str(uuid4()))
    execution = await store.claim("tool-boundary-test", 60, task["task_id"])
    assert execution is not None
    return execution


async def test_authorization_precedes_cache_and_real_mcp_call_count():
    store = Store(settings.database_url.get_secret_value())
    execution = await admitted(store)
    runtime = ToolRuntime(store, ContextEngine(store), settings)
    stderr_evidence = tempfile.TemporaryFile(mode="w+")
    runtime.gateway = MCPDocumentGateway(settings.database_url.get_secret_value(), errlog=stderr_evidence)
    async with store.connection() as conn:
        await conn.execute(
            "INSERT INTO arc_ref.documents(document_id,version,data) VALUES(%s,%s,%s)",
            ("DOC-902", str(uuid4()), Jsonb({"amount": 12000, "attachments": ["invoice"],
              "source": "fictional://test/DOC-902", "secret": "SIMULATED_SECRET_MCP_TEST"})),
        )
    try:
        action = await store.prepare(execution, prepared(
            execution, "document.fetch", {"document_id": "DOC-902"}, EffectClass.READ_ONLY,
        ))
        first = await runtime.invoke(action, execution)
        second = await runtime.invoke(action, execution)
        assert first == second
        assert "SIMULATED_SECRET" not in json.dumps(first)
        assert (await store.ledger(action.tool_call_id))["dispatch_count"] == 1
        changed_arguments = {"document_id": "DOC-001"}
        altered = action.model_copy(update={
            "arguments": changed_arguments,
            "arguments_digest": hashlib.sha256(json.dumps(changed_arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        })
        with pytest.raises(RuntimeFault, match="PREPARED_ACTION_MISMATCH"):
            await runtime.invoke(altered, execution)
        assert (await store.ledger(action.tool_call_id))["dispatch_count"] == 1
        denied = execution.model_copy(update={
            "principal": Principal(principal_id="demo-restricted", role="restricted"),
        })
        version = await store.document_version("DOC-902")
        await store.cache_put(runtime._cache_key(denied, "DOC-902", version), first)
        with pytest.raises(RuntimeFault, match="static tool policy denied"):
            await runtime.invoke(action, denied)
        assert (await store.ledger(action.tool_call_id))["dispatch_count"] == 1
        # 数子进程实际写出的调用标记，不只使用 Runtime 自报 dispatch_count。
        stderr_evidence.seek(0)
        assert stderr_evidence.read().count("ARC_REF_DOCUMENT_READ DOC-902") == 1
        async with store.connection() as conn:
            await conn.execute(
                "UPDATE arc_ref.documents SET data=jsonb_set(data,'{amount}','8000') "
                "WHERE document_id='DOC-902'",
            )
        changed = await runtime.invoke(action, execution)
        assert changed["amount"] == 8000
        assert (await store.ledger(action.tool_call_id))["dispatch_count"] == 2
        stderr_evidence.seek(0)
        assert stderr_evidence.read().count("ARC_REF_DOCUMENT_READ DOC-902") == 2
    finally:
        stderr_evidence.close()
        await store.fail(execution, "TEST_FINISHED")
        async with store.connection() as conn:
            await conn.execute("DELETE FROM arc_ref.documents WHERE document_id='DOC-902'")


async def test_audit_database_failure_prevents_independent_service_request():
    store = Store(settings.database_url.get_secret_value())
    execution = await admitted(store)
    rules = await store.rules()
    report = {
        "document_id": "DOC-902", "document_version": "1", "review_decision": "PASS",
        "missing_documents": [], "fictional": True,
        "citations": [{k: rule[k] for k in ("rule_id", "version", "source")} for rule in rules],
    }
    action = await store.prepare(execution, prepared(
        execution, "report.publish", {"report": report, "mode": "unsafe"}, EffectClass.EXTERNAL_UNSAFE,
    ))
    configuration = settings.model_copy(update={"business_url": "http://127.0.0.1:18084"})
    runtime = ToolRuntime(store, ContextEngine(store), configuration)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "examples.business_service:app",
        "--host", "127.0.0.1", "--port", "18084",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.defpath, "ARC_DATABASE_URL": settings.database_url.get_secret_value()},
    )
    trigger_created = False
    try:
        assert process.stderr is not None
        async with asyncio.timeout(10):
            while True:
                line = await process.stderr.readline()
                assert line, "independent business server exited before readiness"
                if b"Uvicorn running" in line:
                    break
        async with store.connection() as conn:
            # 只针对本次 tool_call_id，避免并发演示被测试故障注入影响。
            await conn.execute(
                "CREATE FUNCTION arc_ref.test_tool_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.tool_call_id::text=TG_ARGV[0] AND NEW.event='tool.dispatch' THEN "
                "RAISE EXCEPTION 'tool audit intentionally unavailable'; END IF; RETURN NEW; END $$",
            )
            await conn.execute(psycopg.sql.SQL(
                "CREATE TRIGGER test_tool_audit_failure BEFORE INSERT ON arc_ref.audit "
                "FOR EACH ROW EXECUTE FUNCTION arc_ref.test_tool_audit_failure({})",
            ).format(psycopg.sql.Literal(action.tool_call_id)))
        trigger_created = True
        with pytest.raises(psycopg.errors.RaiseException, match="tool audit intentionally unavailable"):
            await runtime.invoke(action, execution)
        ledger = await store.ledger(action.tool_call_id)
        assert ledger["state"] == "PREPARED" and ledger["dispatch_count"] == 0
        async with store.connection() as conn:
            requests = await (await conn.execute(
                "SELECT count(*) AS n FROM arc_business.requests WHERE idempotency_key=%s", (action.tool_call_id,),
            )).fetchone()
            effects = await (await conn.execute(
                "SELECT count(*) AS n FROM arc_business.effects WHERE idempotency_key=%s", (action.tool_call_id,),
            )).fetchone()
            assert requests["n"] == effects["n"] == 0
    finally:
        if trigger_created:
            async with store.connection() as conn:
                await conn.execute("DROP TRIGGER test_tool_audit_failure ON arc_ref.audit")
                await conn.execute("DROP FUNCTION arc_ref.test_tool_audit_failure()")
        if process.returncode is None:
            process.terminate()
        await process.wait()
        await store.fail(execution, "TEST_FINISHED")


async def test_report_render_validates_retrieved_references_before_local_commit():
    store = Store(settings.database_url.get_secret_value())
    execution = await admitted(store)
    runtime = ToolRuntime(store, ContextEngine(store), settings)
    rules = await store.rules()
    check = {
        "document_id": "DOC-902", "document_version": "1", "review_decision": "NEEDS_DOCUMENTS",
        "missing_documents": ["approval"],
        "citations": [{k: rule[k] for k in ("rule_id", "version", "source")} for rule in rules],
    }
    try:
        action = await store.prepare(execution, prepared(
            execution, "report.render", {"check": check, "rules": rules}, EffectClass.LOCAL_TRANSACTIONAL,
        ))
        report = await runtime.invoke(action, execution)
        assert report["review_decision"] == "NEEDS_DOCUMENTS" and report["fictional"]
        async with store.connection() as conn:
            row = await (await conn.execute(
                "SELECT count(*) AS n FROM arc_ref.local_effects WHERE tool_call_id=%s", (action.tool_call_id,),
            )).fetchone()
            assert row["n"] == 0
    finally:
        await store.fail(execution, "TEST_FINISHED")
    invalid_execution = await admitted(store)
    try:
        invalid = json.loads(json.dumps(check))
        invalid["citations"][0]["version"] = "invented"
        invalid_action = await store.prepare(invalid_execution, prepared(
            invalid_execution, "report.render", {"check": invalid, "rules": rules},
            EffectClass.LOCAL_TRANSACTIONAL,
        ))
        with pytest.raises(RuntimeFault, match="citation set differs"):
            await runtime.invoke(invalid_action, invalid_execution)
        async with store.connection() as conn:
            count = await (await conn.execute(
                "SELECT count(*) AS n FROM arc_ref.local_effects WHERE tool_call_id=%s", (invalid_action.tool_call_id,),
            )).fetchone()
            assert count["n"] == 0
    finally:
        await store.fail(invalid_execution, "TEST_FINISHED")
