"""独立测试依赖。独立连接提交效果，再等待测试屏障；不与 Runtime 原子提交。"""
import asyncio
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from config.settings import settings

app = FastAPI(title="ARC Reference simulated business dependency")


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str
    task_id: str
    mode: Literal["idempotent", "unsafe", "queryable"]
    report: dict[str, Any]


async def connection() -> psycopg.AsyncConnection[dict[str, Any]]:
    return await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value(), row_factory=dict_row)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/publish")
async def publish(body: Publication) -> dict[str, Any]:
    async with await connection() as conn:
        # 测试依赖自己的幂等锁，不共享 runtime 的事务或 ledger。
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,77))", (body.tool_call_id,))
        await conn.execute("INSERT INTO arc_business.requests(task_id,idempotency_key,mode) VALUES (%s,%s,%s)",
                           (body.task_id, body.tool_call_id, body.mode))
        old = None
        if body.mode != "unsafe":
            old = await (await conn.execute("SELECT effect_id FROM arc_business.effects WHERE idempotency_key=%s AND mode IN ('idempotent','queryable')",
                                            (body.tool_call_id,))).fetchone()
        effect_id = str(old["effect_id"]) if old else str(uuid4())
        if not old:
            await conn.execute("INSERT INTO arc_business.effects(effect_id,task_id,idempotency_key,mode,report) VALUES (%s,%s,%s,%s,%s)",
                               (effect_id, body.task_id, body.tool_call_id, body.mode, Jsonb(body.report)))
    # 已离开事务：此时杀 Worker 不能回滚外部效果。
    if settings.test_mode:
        while True:
            async with await connection() as conn:
                row = await (await conn.execute("SELECT value FROM arc_business.controls WHERE name=%s", (body.tool_call_id,))).fetchone()
                if not row or not row["value"].get("pause", False):
                    break
                await conn.execute("INSERT INTO arc_ref.test_barriers(name,task_id,reached,data) VALUES (%s,%s,true,%s) ON CONFLICT(name) DO UPDATE SET reached=true,data=excluded.data",
                                   ("business:"+body.tool_call_id, body.task_id, Jsonb({"effect_id": effect_id})))
            await asyncio.sleep(0.05)
    return {"effect_id": effect_id, "applied": True}


@app.get("/effects/{key}")
async def query_effect(key: str) -> dict[str, Any]:
    async with await connection() as conn:
        row = await (await conn.execute("SELECT effect_id FROM arc_business.effects WHERE idempotency_key=%s AND mode='queryable'", (key,))).fetchone()
    if not row:
        # 暂时不存在并不是没有执行/将来不会执行的权威证明。
        raise HTTPException(404, "not confirmed")
    return {"applied": True, "effect_id": str(row["effect_id"])}
