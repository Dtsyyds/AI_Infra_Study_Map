"""仅测试模式的发布恢复探针：局部L2/L3验证，不是假称完成document_review。"""
import asyncio
import sys

from config.settings import settings
from core.errors import LeaseLost, RuntimeFault
from runtime.context.engine import ContextEngine
from runtime.storage.store import Store
from runtime.tools.invoker import ToolRuntime


async def main() -> None:
    if not settings.test_mode:
        raise RuntimeError("probe requires ARC_TEST_MODE")
    store = Store(settings.database_url.get_secret_value())
    task_id = sys.argv[1]
    execution = await store.claim("arc-ref-publish-probe", 3, task_id)
    if execution is None:
        raise RuntimeError("probe task not claimable")
    action = await store.pending_action(execution, 0)
    if action is None or action.tool != "report.publish":
        raise RuntimeError("probe requires prepared publish action")
    tools = ToolRuntime(store, ContextEngine(store), settings)
    try:
        result = await tools.invoke(action, execution)
        await store.checkpoint(execution, action, result, {"probe_only": True})
        await store.finish(execution, {"probe_only": True, "publication": result})
    except RuntimeFault as exc:
        try:
            await store.fail(execution, exc.code)
        except LeaseLost:
            pass
    finally:
        await tools.stop()


if __name__ == "__main__":
    asyncio.run(main())
