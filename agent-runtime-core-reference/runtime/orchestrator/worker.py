"""独立可信 Worker；只有 JSON checkpoint 被恢复，不序列化活执行对象。"""
import argparse
import asyncio
import contextlib
import json
import logging
import signal
from typing import Any
from uuid import uuid4

from config.settings import settings
from core.contracts.execution import Execution
from core.errors import LeaseLost, RuntimeFault
from core.hooks import Hooks
from core.kernel import Kernel
from runtime.context.engine import ContextEngine
from runtime.context.repair_context import RepairContext
from runtime.observability.telemetry import TelemetryHook
from runtime.storage.store import Store
from runtime.tools.invoker import ToolRuntime


class CheckpointTestHook:
    def __init__(self, store: Store, execution: Execution) -> None:
        self.store, self.execution = store, execution

    async def emit(self, event: str, fields: dict[str, Any]) -> None:
        if settings.test_mode and event == "after_checkpoint" and settings.test_barrier == f"after_checkpoint:{fields['step_no']}":
            await self.store.barrier(f"{settings.test_barrier}:{self.execution.task_id}", self.execution)
        if settings.test_mode and event == "on_step" and settings.test_barrier == f"before_dispatch:{fields['step_no']}":
            await self.store.barrier(f"{settings.test_barrier}:{self.execution.task_id}", self.execution)


async def execute(store: Store, execution: Execution) -> None:
    context = RepairContext() if execution.request.task_type == "code_repair" else ContextEngine(store)
    tools = ToolRuntime(store, context, settings)
    kernel = Kernel(tools, context, store,
                    hooks=Hooks([TelemetryHook(), CheckpointTestHook(store, execution)]))

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(max(0.2, settings.lease_seconds / 3))
            await store.heartbeat(execution, settings.lease_seconds)
            await store.guard(execution)

    run = asyncio.create_task(kernel.run(execution))
    pulse = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait({run, pulse}, return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            # 不确定租约后取消活执行并停止容器；绝不再提交成功。
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run
            await tools.stop()
            error = pulse.exception()
            if isinstance(error, RuntimeFault) and error.code in {"CANCELLED", "TIMED_OUT"}:
                with contextlib.suppress(LeaseLost):
                    await store.fail(execution, error.code)
                return
            logging.error("LEASE_UNCERTAIN task_id=%s attempt_id=%s", execution.task_id, execution.attempt_id)
            return
        await run
    except LeaseLost:
        logging.warning("LEASE_UNCERTAIN task_id=%s", execution.task_id)
    except RuntimeFault as exc:
        with contextlib.suppress(LeaseLost):
            await store.fail(execution, exc.code)
    except Exception as exc:
        # 异常的类型可观测；不要把外部响应或凭据写进 Trace。
        logging.error("worker_error type=%s task_id=%s", type(exc).__name__, execution.task_id)
        with contextlib.suppress(LeaseLost):
            await store.fail(execution, type(exc).__name__)
    finally:
        pulse.cancel()
        run.cancel()
        await asyncio.gather(pulse, run, return_exceptions=True)
        await tools.stop()


async def main_async(once: bool = False, task_id: str | None = None, worker_id: str | None = None) -> None:
    store = Store(settings.database_url.get_secret_value())
    from runtime.tools.docker_backend import DockerBackend
    docker = DockerBackend(test_mode=settings.test_mode)
    availability = await docker.availability()
    if availability["available"]:
        async def attempt_active(task_id: str, attempt_id: str, fence: int) -> bool | None:
            return await store.attempt_active(task_id, attempt_id, fence)
        removed = await docker.cleanup_orphans(attempt_active)
        # Export only lifecycle/ownership fields, never container environment or command text.
        inspections = []
        for item in docker.evidence:
            clean = {k: v for k, v in item.items() if k != "record"}
            if "record" in item:
                record = item["record"]
                clean["container"] = {"Id": record["Id"], "Name": record["Name"],
                                      "State": record["State"], "Labels": record["Config"].get("Labels")}
            inspections.append(clean)
        logging.info("ARC_REF_STARTUP_CLEANUP %s", json.dumps({"removed": removed, "inspections": inspections}))
    else:
        logging.warning("docker_unavailable startup_cleanup_blocked")
    worker_id = worker_id or "arc-ref-worker-" + str(uuid4())[:8]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(signum,stop.set)
    try:
        while not stop.is_set():
            execution = await store.claim(worker_id, settings.lease_seconds, task_id=task_id)
            if execution:
                running = asyncio.create_task(execute(store,execution))
                stopping = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait({running,stopping},return_when=asyncio.FIRST_COMPLETED)
                    if stopping in done:
                        running.cancel()
                    await asyncio.gather(running,return_exceptions=False)
                except asyncio.CancelledError:
                    if not stop.is_set():
                        raise
                finally:
                    stopping.cancel()
                    await asyncio.gather(stopping,return_exceptions=True)
            if once:
                return
            if not execution:
                try:
                    await asyncio.wait_for(stop.wait(),0.25)
                except TimeoutError:
                    pass
    finally:
        from runtime.storage.pool import close_pools
        await close_pools()
        for signum in (signal.SIGTERM,signal.SIGINT):
            loop.remove_signal_handler(signum)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    # Imported SDKs may already install an ERROR-level root handler. This CLI
    # owns its logging setup; lifecycle/cleanup evidence must remain observable.
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    asyncio.run(main_async(args.once, args.task_id, args.worker_id))


if __name__ == "__main__":
    main()
