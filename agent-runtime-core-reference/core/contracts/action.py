from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class EffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOCAL_TRANSACTIONAL = "LOCAL_TRANSACTIONAL"
    EXTERNAL_IDEMPOTENT = "EXTERNAL_IDEMPOTENT"
    EXTERNAL_QUERYABLE = "EXTERNAL_QUERYABLE"
    EXTERNAL_UNSAFE = "EXTERNAL_UNSAFE"


class PreparedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)      # `extra="forbid"` 拒绝额外字段；`frozen=True` 限制模型字段重新赋值，但不会递归冻结内部字典。后面仍需要校验动作与数据库保存的内容是否一致。
    task_id: str
    step_no: int
    tool_call_id: str
    tool: str
    arguments: dict[str, Any]
    arguments_digest: str
    effect_class: EffectClass
