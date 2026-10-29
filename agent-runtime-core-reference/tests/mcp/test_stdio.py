"""真实官方 SDK/stdin/stdout/subprocess；不把协议通过算成业务数据库通过。"""

import importlib.metadata
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from runtime.tools.mcp_gateway import MCPDocumentGateway


@pytest.mark.asyncio
async def test_real_stdio_discovery_and_call() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("stdio_probe_server.py"))],
    )
    async with Client(parameters, cache=None) as client:
        assert importlib.metadata.version("mcp") == "2.2.0"
        assert client.protocol_version == "2026-07-28"
        result = await client.list_tools()
        assert [tool.name for tool in result.tools] == ["add"]
        response = await client.call_tool("add", {"left": 19, "right": 23})
        assert not response.is_error
        assert response.structured_content == {"computed_sum": 42}
        invalid = await client.call_tool("add", {"left": "invalid", "right": 23})
        assert invalid.is_error


@pytest.mark.asyncio
async def test_business_server_discovers_without_connecting_database() -> None:
    gateway = MCPDocumentGateway("postgresql://arc_ref@127.0.0.1:55439/arc_reference")
    description = await gateway.describe()
    assert description["sdk_version"] == "2.2.0"
    assert description["protocol_version"] == "2026-07-28"
    assert [tool["name"] for tool in description["tools"]] == ["get_document"]
    assert description["tools"][0]["input_schema"]["required"] == ["document_id"]
