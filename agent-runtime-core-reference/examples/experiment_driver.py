"""Trusted HTTP experiment driver. Each attempted submission yields a record even on failure."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from examples.demo import worker
from examples.experiment_metrics import model_accounting, task_timings


def append_record(path: Path, record: dict):
    with path.open("a") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def submit_observe(client, out, body, slot, *, model=False, record_path=None):
    identifier = str(uuid4())
    tick = time.monotonic()
    record = {
        **slot,
        "submission_id": identifier,
        "task_id": None,
        "status": "OBSERVATION_ERROR",
        "http_status": None,
        "timings": {},
        "errors": [],
    }
    events = (record_path.with_name(record_path.stem + "_events.jsonl")
              if record_path is not None else out / "submission_events.jsonl")
    request = {**body, "session_id": identifier}

    def event(kind, **fields):
        append_record(events, {
            "event": kind, "submission_id": identifier,
            "recorded_at": datetime.now(UTC).isoformat(), **fields,
        })

    def incomplete(error):
        record["errors"].append(error)
        record["observation_incomplete"] = True
        record["status"] = "OBSERVATION_ERROR"
        if "model_accounting" in record:
            record["model_accounting"]["delivered"] = False
            record["model_accounting"]["errors"].append("OBSERVATION_INCOMPLETE")

    # Intent precedes HTTP: a crash here leaves an unknown outcome, never proof
    # of rejection. The original idempotency key and request remain available.
    event("SUBMISSION_INTENT", request=request, slot=slot, model=model)
    try:
        response = await client.post(
            "/tasks",
            headers={"Idempotency-Key": identifier},
            json=request,
        )
        record["http_status"] = response.status_code
        if response.status_code != 202:
            record.update(
                status="REJECTED"
                if response.status_code in {400, 401, 403, 409, 413, 422, 429, 503}
                else "HTTP_ERROR",
                rejection=response.json(),
            )
            return record
        task_id = response.json()["task_id"]
        record["task_id"] = task_id
        event("ACCEPTED", task_id=task_id)
        try:
            await worker(task_id, out, wait_seconds=135)
        except (TimeoutError, AssertionError, OSError) as exc:
            record["errors"].append(type(exc).__name__)
        response = await client.get(f"/tasks/{task_id}/diagnostics")
        response.raise_for_status()
        diag = response.json()
        record.update(status=diag["task"]["status"], runtime_status=diag["task"]["status"], diagnostics=diag, timings=task_timings(diag))
        if model:
            record["model_accounting"] = model_accounting(diag)
        # Export every actual BYTEA artifact; require metadata equality.
        import hashlib

        artifacts = []
        if record["status"] == "SUCCEEDED" and not diag.get("artifacts"):
            raise ValueError("SUCCESS_WITHOUT_ARTIFACT")
        for artifact in diag.get("artifacts", []):
            response = await client.get(f"/tasks/{task_id}/artifacts/{artifact['artifact_id']}")
            response.raise_for_status()
            content = response.content
            digest = hashlib.sha256(content).hexdigest()
            if digest != artifact["sha256"] or len(content) != artifact["bytes"]:
                raise ValueError("ARTIFACT_MISMATCH")
            path = out / f"artifact-{artifact['artifact_id']}.json"
            path.write_bytes(content)
            artifacts.append({**artifact, "file": str(path)})
        record["artifacts"] = artifacts
    except asyncio.CancelledError:
        incomplete("CancelledError")
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
        incomplete(type(exc).__name__)
    finally:
        record["submission_wall_seconds"] = time.monotonic() - tick
        if record_path is not None:
            append_record(record_path, record)
        event("OBSERVATION_FINISHED", task_id=record["task_id"], status=record["status"],
              record_file=record_path.name if record_path is not None else None)
    return record
