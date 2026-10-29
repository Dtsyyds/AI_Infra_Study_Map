"""Submit via real API, run an independent worker, export PG-backed evidence."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

from config.settings import settings
from runtime.storage.store import Store


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "repair-demo"
    out.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18081",
        trust_env=False,
        headers={"Authorization": "Bearer " + settings.user_token.get_secret_value()},
    ) as http:
        response = await http.post(
            "/tasks",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "task_type": "code_repair",
                "case_id": "case-01",
                "session_id": str(uuid4()),
                "planner_mode": "fake",
            },
        )
        response.raise_for_status()
        task_id = response.json()["task_id"]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        with (out / "worker.log").open("w") as stream:
            process = await asyncio.create_subprocess_exec(
                ".venv/bin/python",
                "-m",
                "runtime.orchestrator.worker",
                "--once",
                "--task-id",
                task_id,
                env=env,
                stdout=stream,
                stderr=stream,
            )
            async with asyncio.timeout(60):
                await process.wait()
        response = await http.get(f"/tasks/{task_id}/diagnostics")
        response.raise_for_status()
        diag = response.json()
        (out / "diagnostics.json").write_text(json.dumps(diag, indent=2))
        artifacts = []
        store = Store(settings.database_url.get_secret_value())
        for record in diag["artifacts"]:
            data = await http.get(f"/tasks/{task_id}/artifacts/{record['artifact_id']}")
            data.raise_for_status()
            database = await store.artifact(
                task_id, record["artifact_id"], diag["task"]["principal_id"]
            )
            assert data.content == database
            path = out / (record["artifact_id"] + ".json")
            path.write_bytes(database)
            artifacts.append(
                {**record, "sha256": hashlib.sha256(database).hexdigest(), "file": str(path)}
            )
        result = {
            "scope": "real API/PG/worker; Fake proposal mode; no sandbox substitution",
            "task_id": task_id,
            "runtime_status": diag["task"]["status"],
            "error_code": diag["task"]["error_code"],
            "business_outcome": (diag["task"]["result"] or {}).get("business_outcome"),
            "attempt_ids": [a["attempt_id"] for a in diag["attempts"]],
            "tool_call_ids": [a["tool_call_id"] for a in diag["actions"]],
            "candidate_numbers": [c["candidate_no"] for c in diag["repair_candidates"]],
            "verification_run_ids": [
                v["verification_run_id"] for v in diag["repair_verifications"]
            ],
            "artifacts": artifacts,
            "worker_exit_code": process.returncode,
            "real_model_calls": len(diag["model_calls"]),
        }
        (out / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    from runtime.storage.pool import close_pools

    await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
