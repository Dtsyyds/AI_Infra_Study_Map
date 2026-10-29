"""独立 stdio MCP 2.2 server：业务资料来自 PostgreSQL，只读事务。"""

from __future__ import annotations

import os
import sys
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

server = MCPServer("Agent Runtime Core Reference documents", version="1.0.0", log_level="ERROR")


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    version: str
    amount: float = Field(ge=0)
    attachments: list[str]
    source: str


@server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def get_document(document_id: Annotated[str, Field(pattern=r"^DOC-[0-9]{3}$")]) -> Document:
    """读取明确标注为虚构的采购文档；输入不接受身份、SQL或连接信息。"""
    database_url = os.environ.get("ARC_DATABASE_URL")
    if not database_url:
        raise ToolError("document storage is not configured")
    async with await AsyncConnection.connect(database_url, row_factory=dict_row) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        cursor = await conn.execute(
            "SELECT document_id, version, data FROM arc_ref.documents WHERE document_id=%s",
            (document_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ToolError("document not found")
        data = row["data"]
        # 不把数据库意外新增的秘密字段直接透传给网关或模型。
        document = Document(
            document_id=row["document_id"], version=row["version"],
            amount=data["amount"], attachments=data["attachments"], source=data["source"],
        )
    print(f"ARC_REF_DOCUMENT_READ {document.document_id} {document.version}", file=sys.stderr, flush=True)
    return document


if __name__ == "__main__":
    server.run(transport="stdio")
