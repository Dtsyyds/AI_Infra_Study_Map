"""Create and remove only this probe's new empty database; never reset reference data."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

from config.settings import settings


def main():
    state = json.loads(Path("TASK_STATUS.json").read_text())
    out = Path("reports") / state["run_id"] / "migration-probe.json"
    name = "arc_ref_migration_" + uuid4().hex[:12]
    result: dict[str, Any] = {"database": name, "created": False, "removed": False, "steps": []}
    dsn = settings.database_url.get_secret_value()
    with psycopg.connect(dsn, autocommit=True) as conn:
        permission_row = conn.execute(
            "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not permission_row or not permission_row[0]:
            result["status"] = "BLOCKED_CREATE_DATABASE_PERMISSION"
            out.write_text(json.dumps(result, indent=2))
            return
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            result["created"] = True
            parts = urlsplit(dsn)
            child_dsn = urlunsplit(
                (parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment)
            )
            env = dict(os.environ, ARC_DATABASE_URL=child_dsn)
            env.pop("PYTHONPATH", None)
            for revision in ("0001_reference", "head", "head"):
                command = [".venv/bin/alembic", "upgrade", revision]
                process = subprocess.run(command, env=env, capture_output=True, text=True)
                result["steps"].append(
                    {
                        "command": command,
                        "exit_code": process.returncode,
                        "stdout": process.stdout,
                        "stderr": process.stderr,
                    }
                )
                if process.returncode:
                    raise RuntimeError("migration failed")
            with psycopg.connect(child_dsn) as child:
                assert child.execute("SELECT version_num FROM alembic_version").fetchone() == (
                    "0002_repair",
                )
                assert child.execute("SELECT count(*) FROM arc_ref.documents").fetchone() == (3,)
                assert child.execute(
                    "SELECT count(*) FROM arc_ref.repair_candidates"
                ).fetchone() == (0,)
            result["status"] = "PASS"
        finally:
            if result["created"]:
                conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                result["removed"] = True
            out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
