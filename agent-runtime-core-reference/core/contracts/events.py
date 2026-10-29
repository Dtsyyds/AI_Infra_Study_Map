from typing import Any, Protocol


class Hook(Protocol):
    async def emit(self, event: str, fields: dict[str, Any]) -> None: ...
