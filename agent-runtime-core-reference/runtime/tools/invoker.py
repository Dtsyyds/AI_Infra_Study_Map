"""L2：授权先于缓存，持久派发屏障先于执行，外部不确定性保守处理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx

from config.settings import Settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.context import ContextProvider
from core.contracts.execution import Execution
from core.errors import RuntimeFault
from runtime.context.engine import validate_citations
from runtime.observability.telemetry import record_tool
from runtime.security import authorize, guard_output
from runtime.storage.store import Store
from runtime.tools.docker_backend import DockerBackend, DockerUnavailable
from runtime.tools.mcp_gateway import MCPDocumentGateway
from runtime.tools.registry import CheckResult, Report, ToolRegistry


class ToolRuntime:
    def __init__(self, store: Store, context_engine: ContextProvider, settings: Settings) -> None:
        self.store = store
        self.context_engine = context_engine
        self.settings = settings
        self.registry = ToolRegistry()
        self.gateway = MCPDocumentGateway(settings.database_url.get_secret_value())
        self.docker = DockerBackend(test_mode=settings.test_mode, image=settings.docker_image)

    async def stop(self) -> None:
        await self.docker.cancel_all()

    def _cache_key(self, execution: Execution, document_id: str, version: str) -> str:
        key = {
            "principal": execution.principal.principal_id,
            "scope": execution.principal.scope,
            "document_id": document_id, "document_version": version,
            "tool": "document.fetch", "tool_version": "1",
        }
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()

    async def invoke(self, action: PreparedAction, execution: Execution) -> dict[str, Any]:
        try:
            authorize(execution.principal, action.tool, execution.request.task_type)        # 权限检查,命中缓存必须经过授权
        except RuntimeFault:
            record_tool(action.tool, "denied")
            # 拒绝本身不依赖审计可用性；数据库故障时无持久拒绝审计是明确观测缺口。
            try:
                await self.store.audit(execution.principal, "tool.denied", execution, action)
            except Exception:
                pass
            raise
        if action.task_id != execution.task_id:
            raise RuntimeFault("TASK_ID_MISMATCH")
        arguments = self.registry.validate(action)      # `validate()`：工具是否在白名单、参数是否合法、摘要是否匹配、副作用分类是否正确。
        # 缓存/人工确认返回也必须对应已保存的同一个动作，不能只检查参数摘要自身一致。
        saved_action = await self.store.pending_action(execution, action.step_no)       # 本次执行的是否仍然是之前保存的完整动作
        if saved_action != action:
            raise RuntimeFault("PREPARED_ACTION_MISMATCH")
        ledger = await self.store.ledger(action.tool_call_id)
        if ledger["state"] in {"COMMITTED", "CONFIRMED_APPLIED"}:
            return guard_output(ledger["result"])
        if ledger["state"] == "UNKNOWN":
            raise RuntimeFault("NEEDS_REVIEW")
        if ledger["state"] == "DISPATCHED":
            if action.effect_class == EffectClass.EXTERNAL_UNSAFE:
                await self._unknown(action, execution)
            if action.effect_class == EffectClass.EXTERNAL_QUERYABLE:
                confirmed = await self._query(action, execution)
                if confirmed is not None:
                    return guard_output(confirmed)
                await self._unknown(action, execution)
        if action.tool == "document.fetch":
            version = await self.store.document_version(arguments["document_id"])
            cache_key = self._cache_key(execution, arguments["document_id"], version)
            cached = await self.store.cache_get(cache_key)
            if cached is not None:
                record_tool(action.tool, "cached")
                return guard_output(cached)

        # 真实派发前必须提交 Ledger 与 Audit。失败直接传播；绝不能执行后补审计。
        dispatched = await self.store.dispatch(execution, action)
        if dispatched["state"] in {"COMMITTED", "CONFIRMED_APPLIED"}:
            return guard_output(dispatched["result"])
        remaining = await self.store.remaining_ms(execution)
        timeout = min(15.0, remaining / 1000)
        if timeout <= 0:
            raise RuntimeFault("TIMED_OUT")
        try:
            if action.tool.startswith("repair."):
                from runtime.tools.repair_tools import invoke_repair
                result = await invoke_repair(self.store, self.docker, self.settings, action, execution, arguments, timeout)
            elif action.tool == "document.fetch":     # 通过受信 MCP stdio 获取文档；授权在上述 ToolRuntime 边界完成。
                async with asyncio.timeout(timeout):
                    result = await self.gateway.fetch(arguments["document_id"])
                result = guard_output(result)
                # 版本同时包含内容指纹；即使测试只改金额没递增版本也不会命中旧结果。
                after_version = await self.store.document_version(arguments["document_id"])
                if after_version == version:
                    await self.store.cache_put(cache_key, result)
            elif action.tool == "document.check":       # 调用 Docker 容器检查文档内容；容器内的检查逻辑由业务方自行实现。
                try:
                    raw = await self.docker.check(
                        arguments["document"], arguments["rules"], task_id=execution.task_id,
                        attempt_id=execution.attempt_id, fence=execution.fence,
                        timeout_seconds=min(10.0, timeout),
                    )
                except DockerUnavailable as exc:
                    raise RuntimeFault("DOCKER_UNAVAILABLE", str(exc)) from exc
                result = CheckResult.model_validate(raw).model_dump(mode="json")
                validate_citations(result, arguments["rules"])
            elif action.tool == "report.render":        # 计算 Report JSON；本地事务不依赖外部服务，直接计算结果。
                validate_citations(arguments["check"], arguments["rules"])
                # 本地业务效果只在下一 checkpoint 事务写入；这里仅计算 JSON。
                result = Report.model_validate(arguments["check"]).model_dump(mode="json")
            elif action.tool == "report.publish":       # 通过外部服务发布 Report；恢复策略由 idempotent/queryable/unsafe 类别决定。
                result = await self._publish(action, execution, arguments, timeout)
            else:
                raise RuntimeFault("UNKNOWN_TOOL")
        except BaseException:
            record_tool(action.tool, "error")
            raise
        record_tool(action.tool, "success")
        return guard_output(result)

    async def _unknown(self, action: PreparedAction, execution: Execution) -> None:
        await self.store.unknown(execution, action)
        record_tool(action.tool, "unknown")
        raise RuntimeFault("NEEDS_REVIEW", "external effect requires reconciliation")

    async def _query(self, action: PreparedAction, execution: Execution) -> dict[str, Any] | None:
        timeout = min(5.0, (await self.store.remaining_ms(execution)) / 1000)
        if timeout <= 0:
            return None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
                response = await client.get(f"{self.settings.business_url}/effects/{action.tool_call_id}")
                if response.status_code == 200:
                    result: dict[str, Any] = response.json()
                    if result.get("applied") is True:
                        return result
        except (httpx.HTTPError, ValueError):
            pass
        # 404/超时只能表示此刻查询无肯定证据，不能推出原请求永远不会生效。
        return None

    async def _publish(
        self, action: PreparedAction, execution: Execution, arguments: dict[str, Any], max_seconds: float,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=max_seconds) as client:
                response = await client.post(
                    f"{self.settings.business_url}/publish",
                    headers={"Idempotency-Key": action.tool_call_id},
                    json={"tool_call_id": action.tool_call_id, "task_id": execution.task_id,
                          "mode": arguments["mode"], "report": arguments["report"]},
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
        except (httpx.HTTPError, ValueError) as exc:
            if action.effect_class == EffectClass.EXTERNAL_IDEMPOTENT:
                raise RuntimeFault("RETRYABLE", "idempotent publication response unavailable") from exc
            if action.effect_class == EffectClass.EXTERNAL_QUERYABLE:
                confirmed = await self._query(action, execution)
                if confirmed is not None:
                    return confirmed
            await self._unknown(action, execution)
            raise AssertionError("unreachable") from exc
