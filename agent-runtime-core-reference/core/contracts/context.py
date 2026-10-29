from typing import Any, Protocol

from pydantic import BaseModel

from core.contracts.execution import Execution


class ContextBundle(BaseModel):
    rules: list[dict[str, Any]]
    protected_facts: dict[str, Any]
    working_memory: list[dict[str, Any]] = []
    summary: str = ""
    estimated_tokens: int = 0
    session_version: int = 0


class ContextProvider(Protocol):
    async def build(self, execution: Execution) -> ContextBundle: ...
    def observe(self, context: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]: ...
