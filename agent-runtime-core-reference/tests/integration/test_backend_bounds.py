import asyncio

import httpx
import pytest
from psycopg_pool import PoolTimeout

from api.main import app
from config.settings import settings
from runtime.storage.pool import get_pool


async def test_chunked_body_is_bounded():
    async def chunks():
        yield b"x" * 40000
        yield b"x" * 40000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/tasks", content=chunks())
        assert response.status_code == 413 and response.json()["error"] == "BODY_TOO_LARGE"
        assert (await client.get("/health/live")).json() == {"live": True}


async def test_pool_has_four_connections_and_releases_on_cancellation():
    pool = await get_pool(settings.database_url.get_secret_value())
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def hold():
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
            await entered.put(True)
            await release.wait()

    tasks = [asyncio.create_task(hold()) for _ in range(4)]
    try:
        for _ in tasks:
            await asyncio.wait_for(entered.get(), 3)
        with pytest.raises(PoolTimeout):
            async with pool.connection():
                pass
        assert pool.get_stats()["pool_size"] <= 4
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with pool.connection() as conn:
        assert (await (await conn.execute("SELECT 1 AS n")).fetchone())["n"] == 1
