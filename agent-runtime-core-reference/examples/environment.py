"""Read-only, credential-free environment evidence for the current implementation run."""

import asyncio
import importlib.metadata
import json
import os
import platform
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from config.settings import settings
from runtime.tools.docker_backend import IMAGE, DockerBackend


async def command(*argv):
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return {
        "argv": argv,
        "exit_code": proc.returncode,
        "stdout": stdout.decode().strip(),
        "stderr": stderr.decode().strip(),
    }


async def inspect() -> dict:
    backend = DockerBackend()
    docker = await backend.availability()
    docker["client"] = await command("docker", "--version")
    docker["context"] = await command("docker", "context", "show")
    sock = Path("/var/run/docker.sock")
    if await asyncio.to_thread(sock.exists):
        info = await asyncio.to_thread(sock.stat)
        docker["socket"] = {
            "mode": oct(stat.S_IMODE(info.st_mode)),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "readable": os.access(sock, os.R_OK),
            "writable": os.access(sock, os.W_OK),
        }
    docker["configured_image"] = IMAGE
    docker["local_image_verified"] = False
    if docker["available"]:
        code, output, error = await backend._docker("image", "inspect", IMAGE)
        docker["image_inspect_exit_code"] = code
        if code == 0:
            records = json.loads(output)
            expected = IMAGE.split("@", 1)[1]
            digests = records[0].get("RepoDigests", [])
            docker["local_image_verified"] = any(
                value.endswith("@" + expected) for value in digests
            )
            docker["local_image"] = {
                "id": records[0]["Id"],
                "repo_digests": digests,
                "architecture": records[0]["Architecture"],
                "os": records[0]["Os"],
            }
        else:
            docker["image_inspect_error"] = error.strip()
    docker["uid"] = os.getuid()
    docker["groups"] = os.getgroups()
    async with await psycopg.AsyncConnection.connect(
        settings.database_url.get_secret_value()
    ) as conn:
        row = await (await conn.execute("SELECT version(),current_setting('timezone')")).fetchone()
    packages = {
        d.metadata["Name"]: d.version
        for d in importlib.metadata.distributions()
        if d.metadata["Name"]
    }
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "workspace": str(Path.cwd()),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "git": await command("git", "rev-parse", "HEAD"),
        "formal_learning_project_accessed": False,
        "postgresql": {
            "version": row[0] if row else None,
            "timezone": row[1] if row else None,
            "host": "127.0.0.1",
            "port": 55439,
            "database": "arc_reference",
            "deployment": "existing workspace-local binaries",
        },
        "mcp": {
            "sdk": importlib.metadata.version("mcp"),
            "transport": "real stdio",
            "negotiated_protocol": "see raw MCP tests; not inferred from installed version",
        },
        "docker": docker,
        "model": {
            "enabled": settings.model_enabled,
            "model": settings.model_name or None,
            "request_limit": settings.model_request_limit,
            "token_limit": settings.model_token_limit,
            "remote_execution_performed_by_environment_probe": False,
            "price": None,
        },
        "credential_note": "No secret values exported. Only local demo identity used.",
    }


def main():
    run = (
        sys.argv[1]
        if len(sys.argv) > 1
        else json.loads(Path("TASK_STATUS.json").read_text())["run_id"]
    )
    directory = Path("reports") / run
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "environment.json").write_text(json.dumps(asyncio.run(inspect()), indent=2))
    print(str(directory / "environment.json"))


if __name__ == "__main__":
    main()
