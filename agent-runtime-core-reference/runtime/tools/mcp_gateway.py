"""Worker 内部 MCP 网关；只公开一个静态业务工具，不暴露通用 MCP 转发。"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path
from typing import Any, TextIO

from mcp import Client, StdioServerParameters, stdio_client

from examples.mcp_server import Document


class MCPDocumentGateway:
    def __init__(self, database_url: str, *, errlog: TextIO | None = None) -> None:
        self._database_url = database_url
        self._errlog = errlog
        self.last_protocol_version: str | None = None

    def parameters(self) -> StdioServerParameters:
        root = Path(__file__).resolve().parents[2]
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "examples.mcp_server"],
            cwd=root,
            # 这是受信本地资料服务；Docker 沙箱不获得该环境变量。
            env={"ARC_DATABASE_URL": self._database_url},
        )

    def _client(self) -> Client:
        transport = stdio_client(self.parameters(), errlog=self._errlog or sys.stderr)
        return Client(transport, read_timeout_seconds=15, cache=None)

    async def describe(self) -> dict[str, Any]:
        async with self._client() as client:
            tools = await client.list_tools()
            self.last_protocol_version = client.protocol_version
            return {
                "sdk_version": importlib.metadata.version("mcp"),
                "protocol_version": client.protocol_version,
                "tools": [{"name": tool.name, "input_schema": tool.input_schema} for tool in tools.tools],
            }

    async def fetch(self, document_id: str) -> dict[str, Any]:
        async with self._client() as client:
            self.last_protocol_version = client.protocol_version
            if client.protocol_version != "2026-07-28":
                raise RuntimeError("MCP protocol baseline mismatch")
            discovery = await client.list_tools()
            if {tool.name for tool in discovery.tools} != {"get_document"}:
                raise RuntimeError("unexpected MCP tool discovery")
            result = await client.call_tool("get_document", {"document_id": document_id})
            if result.is_error or result.structured_content is None:
                # 不转发服务端异常全文，以免连接信息被插件错误包含。
                raise RuntimeError("MCP document read failed")
            return Document.model_validate(result.structured_content).model_dump(mode="json")
