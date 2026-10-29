from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
ACTIVE = {"QUEUED", "RUNNING", "NEEDS_REVIEW"}


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["document_review", "code_repair"] = "document_review"
    document_id: str | None = Field(default=None, pattern=r"^DOC-[0-9]{3}$")
    case_id: str | None = Field(default=None, pattern=r"^case-[0-9]{2}$")
    planner_mode: Literal["fake", "real"] = "fake"
    repair_strategy: Literal["single", "context", "loop"] = "loop"
    session_id: str = Field(min_length=1, max_length=80)
    budget_ms: int = Field(default=120000, ge=1000, le=1800000, strict=True)
    publish_mode: Literal["none", "idempotent", "unsafe", "queryable"] = "none"

    @model_validator(mode="after")
    def task_fields(self) -> "TaskRequest":
        if self.task_type == "document_review":
            if self.document_id is None or self.case_id is not None or self.planner_mode != "fake" or self.repair_strategy != "loop":
                raise ValueError("invalid review request")
        elif self.case_id is None or self.document_id is not None or self.publish_mode != "none":
            raise ValueError("invalid repair request")
        return self
