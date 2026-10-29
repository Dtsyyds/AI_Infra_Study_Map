"""业务 MCP 测试必须真实读 PostgreSQL，不能用协议探针替代。"""

import pytest
from psycopg import AsyncConnection, OperationalError
from psycopg.types.json import Jsonb

from config.settings import settings
from runtime.tools.mcp_gateway import MCPDocumentGateway


@pytest.mark.asyncio
async def test_actual_database_document_changes_are_observed() -> None:
    dsn = settings.database_url.get_secret_value()
    try:
        connection = await AsyncConnection.connect(dsn)
    except OperationalError:
        pytest.skip("BLOCKED: PostgreSQL unavailable for real MCP document test")
    async with connection as conn:
        # 本测试独占 DOC-901，不覆盖教学样例或任何未知数据库记录。
        await conn.execute(
            "INSERT INTO arc_ref.documents(document_id,version,data) VALUES(%s,%s,%s)",
            ("DOC-901", "test-v1", Jsonb({
                "amount": 12000, "attachments": ["invoice"], "source": "fictional://test/DOC-901",
                "internal_secret": "SIMULATED_SECRET_MUST_NOT_LEAK",
            })),
        )
        await conn.commit()
        gateway = MCPDocumentGateway(dsn)
        try:
            document = await gateway.fetch("DOC-901")
            assert document["amount"] == 12000
            assert document["attachments"] == ["invoice"]
            assert "internal_secret" not in document
            await conn.execute(
                "UPDATE arc_ref.documents SET version=%s, data=jsonb_set(data,'{amount}','8000') "
                "WHERE document_id=%s", ("test-v2", "DOC-901"),
            )
            await conn.commit()
            changed = await gateway.fetch("DOC-901")
            assert changed["amount"] == 8000
            assert changed["version"] == "test-v2"
            assert gateway.last_protocol_version == "2026-07-28"
        finally:
            await conn.execute("DELETE FROM arc_ref.documents WHERE document_id=%s", ("DOC-901",))
            await conn.commit()
