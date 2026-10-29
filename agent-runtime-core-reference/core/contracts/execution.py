from datetime import datetime
from typing import Any

from pydantic import BaseModel

from core.contracts.identity import Principal
from core.contracts.task import TaskRequest


class Execution(BaseModel):
    task_id: str
    attempt_id: str
    fence: int
    deadline_at: datetime
    principal: Principal
    request: TaskRequest
    checkpoint: int = -1
    context: dict[str, Any] = {}
    session_version: int = 0
