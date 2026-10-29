from typing import Any, Protocol

from core.contracts.action import PreparedAction
from core.contracts.execution import Execution


class ToolInvoker(Protocol):
    async def invoke(self, action: PreparedAction, execution: Execution) -> dict[str, Any]: ...
    async def stop(self) -> None: ...
