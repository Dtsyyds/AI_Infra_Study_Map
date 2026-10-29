"""Bounded per-process/event-loop pools; cross-worker admission remains in PostgreSQL."""

import asyncio
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_POOLS: dict[tuple[asyncio.AbstractEventLoop, str], AsyncConnectionPool[AsyncConnection[dict[str, Any]]]] = {}


async def get_pool(dsn: str) -> AsyncConnectionPool[AsyncConnection[dict[str, Any]]]:
    key = (asyncio.get_running_loop(), dsn)
    pool = _POOLS.get(key)
    if pool is None:
        pool = AsyncConnectionPool(
            dsn,
            min_size=0,
            max_size=4,
            max_waiting=16,
            timeout=2,
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=False,
        )
        _POOLS[key] = pool
        await pool.open()
    return pool


async def close_pools() -> None:
    loop = asyncio.get_running_loop()
    for key in list(_POOLS):
        if key[0] is loop:
            await _POOLS.pop(key).close()
