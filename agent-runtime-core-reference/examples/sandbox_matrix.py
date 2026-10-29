"""Five categories x five real container probes. Blocked slots are never execution samples."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from examples.repair_recovery import scenario as repair_scenario
from runtime.storage.pool import close_pools
from runtime.tools.docker_backend import DockerBackend

NORMAL = 'print("ARC_REF_STARTED", flush=True)'
WAIT = 'import signal\nprint("ARC_REF_STARTED",flush=True)\nprint("ARC_REPAIR_TEST_ENTERED",flush=True)\nwhile True: signal.pause()'
MEMORY = 'print("ARC_REF_STARTED",flush=True)\na=[]\nwhile True: a.append(bytearray(8*1024*1024))'


async def scenario(kind):
    if kind == "process_kill_recovery":
        return await repair_scenario("test_running")
    backend = DockerBackend(test_mode=True)
    started = asyncio.Event()

    async def on_started(name):
        assert (await backend.inspect_state(name))["status"] == "RUNNING"
        started.set()

    task_id, attempt_id, run_id = (str(uuid4()) for _ in range(3))
    operation = asyncio.create_task(
        backend.run_fixed(
            NORMAL if kind == "normal" else MEMORY if kind == "memory" else WAIT,
            b"",
            task_id=task_id,
            attempt_id=attempt_id,
            run_id=run_id,
            fence=1,
            wall_seconds=2 if kind == "timeout" else 15,
            output_bytes=262144,
            on_test_started=on_started if kind in {"cancel", "timeout"} else None,
        )
    )
    try:
        if kind == "cancel":
            await asyncio.wait_for(started.wait(), 10)
            operation.cancel()
            try:
                await operation
                raise AssertionError("cancel did not interrupt")
            except asyncio.CancelledError:
                pass
            result = next(
                row for row in reversed(backend.evidence) if row.get("operation") == "run_finished"
            )
        else:
            result = await operation
        assert result["cleanup_confirmed"] and "ARC_REF_STARTED" in result["stdout"]
        assert any(row.get("status") == "STOPPED" for row in result["inspections"])
        assert any(row.get("status") == "ABSENT" for row in result["inspections"])
        if kind == "normal":
            assert result["exit_code"] == 0 and not result["resource_error"]
        elif kind == "timeout":
            assert started.is_set() and result["timed_out"]
        elif kind == "memory":
            assert result["resource_error"] and result["container"]["State"]["OOMKilled"]
        elif kind == "cancel":
            assert result["cancelled"]
        return {
            "status": "PASS",
            "kind": kind,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "verification_run_id": run_id,
            "scope": "trusted fixed lifecycle probe, no model/candidate result",
            "result": result,
        }
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await backend.cancel_all()


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "sandbox-matrix"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.jsonl"
    if path.exists():
        raise RuntimeError("new run_id required")
    available = await DockerBackend().availability()
    try:
        for kind in ("normal", "timeout", "memory", "cancel", "process_kill_recovery"):
            for repeat in range(1, 6):
                row = {"kind": kind, "repeat": repeat, "executed": available["available"]}
                if not available["available"]:
                    row.update(status="BLOCKED", reason=available["reason"])
                else:
                    try:
                        row.update(await scenario(kind))
                    except Exception as exc:
                        row.update(status="FAIL", error_type=type(exc).__name__)
                        with path.open("a") as stream:
                            stream.write(json.dumps(row) + "\n")
                        raise
                with path.open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
                print(
                    json.dumps(
                        {k: v for k, v in row.items() if k not in {"result", "before", "after"}}
                    )
                )
    finally:
        await close_pools()


if __name__ == "__main__":
    asyncio.run(run())
