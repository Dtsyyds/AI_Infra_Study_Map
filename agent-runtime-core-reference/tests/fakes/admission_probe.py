"""Trusted admission crash probe. Does not claim work or execute candidate source."""

import asyncio
import sys

from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from runtime.storage.store import Store


async def run(identifier):
    store = Store(settings.database_url.get_secret_value())
    task = await store.submit(
        Principal(principal_id="demo-reviewer"),
        TaskRequest(document_id="DOC-002", session_id=identifier),
        identifier,
    )
    async with store.connection() as conn:
        await conn.execute(
            "INSERT INTO arc_ref.test_barriers(name,reached,data) VALUES(%s,true,%s)",
            ("admission:" + identifier, Jsonb({"task_id": task["task_id"]})),
        )
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
