from typing import Any, Protocol

from core.contracts.repair import Patch


class PatchPlanner(Protocol):
    async def propose(
        self, context: dict[str, Any], *, max_seconds: float
    ) -> tuple[Patch, dict[str, Any]]: ...
