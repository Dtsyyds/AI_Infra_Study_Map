from uuid import uuid4

import psycopg
import pytest

from config.settings import settings

pytestmark = pytest.mark.integration


async def test_migration_schema_and_append_only_audit():
    async with await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value()) as conn:
        async with conn.transaction():
            cur = await conn.execute("SELECT count(*) FROM arc_ref.documents")
            assert (await cur.fetchone())[0] == 3
            cur = await conn.execute(
                "INSERT INTO arc_ref.audit(principal_id,event,data) VALUES(%s,'schema_test','{}') RETURNING audit_id",
                (f"schema-{uuid4()}",),
            )
            audit_id = (await cur.fetchone())[0]
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                async with conn.transaction():
                    await conn.execute("UPDATE arc_ref.audit SET event='changed' WHERE audit_id=%s", (audit_id,))
            cur = await conn.execute("SELECT event FROM arc_ref.audit WHERE audit_id=%s", (audit_id,))
            assert (await cur.fetchone())[0] == "schema_test"
