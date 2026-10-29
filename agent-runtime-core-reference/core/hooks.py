"""遥测观察者无法拿到 ToolInvoker 或 Journal，且失败不改变持久业务结果。"""
import logging
from typing import Any

from core.contracts.events import Hook


class Hooks:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks = hooks or []

    async def emit(self, event: str, **fields: Any) -> None:
        for hook in self.hooks:
            try:
                await hook.emit(event, fields)
            except Exception:
                # 不打印异常内容：第三方遥测异常可能包含敏感参数。
                logging.getLogger(__name__).warning("telemetry_hook_failed event=%s", event)
