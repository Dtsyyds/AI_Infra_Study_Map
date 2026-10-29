"""Exercise every developer CLI command through the real local HTTP service."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import httpx


def main():
    out = Path("reports") / json.loads(Path("TASK_STATUS.json").read_text())["run_id"] / "cli-probe"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    def cli(*args):
        argv = [".venv/bin/python", "-m", "examples.cli", *args]
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
        records.append(
            {
                "command": argv,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        )
        (out / "commands.json").write_text(json.dumps(records, indent=2))
        assert proc.returncode == 0
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    task = cli("submit", "--case", "case-01")
    task_id = task["task_id"]
    assert cli("status", task_id)["status"] == "QUEUED"
    cli("cancel", task_id)
    assert cli("wait", task_id, "--seconds", "5")["status"] == "CANCELLED"
    diag = cli("diagnostics", task_id)
    assert diag["task"]["status"] == "CANCELLED"
    previous = json.loads((out.parent / "repair-demo/result.json").read_text())
    artifact = previous["artifacts"][0]
    download = out / "downloaded-artifact.json"
    cli("artifact", previous["task_id"], artifact["artifact_id"], "--output", str(download))
    assert hashlib.sha256(download.read_bytes()).hexdigest() == artifact["sha256"]
    with httpx.Client(base_url="http://127.0.0.1:18081", trust_env=False) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        schema = client.get("/openapi.json")
        assert live.status_code == 200
        expected_ready = ready.json()["database"] and ready.json()["docker"]
        assert ready.status_code == (200 if expected_ready else 503)
        assert ready.json()["ready"] == expected_ready
        assert schema.status_code == 200 and "/tasks" in schema.json()["paths"]
    result = {
        "status": "PASS",
        "scope": "HTTP CLI contract; no candidate execution",
        "task_id": task_id,
        "artifact_sha256": artifact["sha256"],
        "health_live": {"http_status": live.status_code, "body": live.json()},
        "health_ready": {"http_status": ready.status_code, "body": ready.json()},
        "openapi_paths": sorted(schema.json()["paths"]),
    }
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
