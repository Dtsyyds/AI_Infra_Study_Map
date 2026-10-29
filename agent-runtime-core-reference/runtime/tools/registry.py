"""工具白名单与每个工具的输入契约；LLM 无法添加后端或改变副作用分类。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.contracts.action import EffectClass, PreparedAction
from core.errors import RuntimeFault
from examples.mcp_server import Document
from runtime.security import guard_input


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rule(StrictModel):
    rule_id: str
    version: str
    source: str
    text: str
    rule_type: Literal["approval_threshold", "invoice_required"]
    threshold: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def threshold_required(self) -> Rule:
        if self.rule_type == "approval_threshold" and self.threshold is None:
            raise ValueError("approval rule requires a threshold")
        return self


class Citation(StrictModel):
    rule_id: str
    version: str
    source: str


class CheckResult(StrictModel):
    document_id: str
    document_version: str
    review_decision: Literal["PASS", "NEEDS_DOCUMENTS"]
    missing_documents: list[Literal["approval", "invoice"]]
    citations: list[Citation]


class Report(CheckResult):
    fictional: Literal[True] = True


class FetchArguments(StrictModel):
    document_id: str = Field(pattern=r"^DOC-[0-9]{3}$")


class CheckArguments(StrictModel):
    document: Document
    rules: list[Rule] = Field(min_length=1, max_length=10)


class RenderArguments(StrictModel):
    check: CheckResult
    rules: list[Rule] = Field(min_length=1, max_length=10)


class PublishArguments(StrictModel):
    report: Report
    mode: Literal["idempotent", "unsafe", "queryable"]


class RepairArguments(StrictModel):
    candidate_no: int = Field(ge=0, le=3, strict=True)


class ToolRegistry:
    MODE_EFFECT = {
        "idempotent": EffectClass.EXTERNAL_IDEMPOTENT,
        "unsafe": EffectClass.EXTERNAL_UNSAFE,
        "queryable": EffectClass.EXTERNAL_QUERYABLE,
    }
    SPECS: dict[str, tuple[type[BaseModel], set[EffectClass]]] = {
        **{name: (RepairArguments, {EffectClass.READ_ONLY}) for name in ("repair.baseline", "repair.generate", "repair.verify", "repair.report")},
        "document.fetch": (FetchArguments, {EffectClass.READ_ONLY}),
        "document.check": (CheckArguments, {EffectClass.READ_ONLY}),
        "report.render": (RenderArguments, {EffectClass.LOCAL_TRANSACTIONAL}),
        "report.publish": (PublishArguments, set(MODE_EFFECT.values())),
    }

    def validate(self, action: PreparedAction) -> dict[str, Any]:
        specification = self.SPECS.get(action.tool)
        if specification is None:
            raise RuntimeFault("UNKNOWN_TOOL")
        model, effects = specification
        if action.effect_class not in effects:
            raise RuntimeFault("EFFECT_CLASS_MISMATCH")
        encoded = json.dumps(action.arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != action.arguments_digest:
            raise RuntimeFault("ARGUMENT_DIGEST_MISMATCH")
        arguments = model.model_validate(guard_input(action.arguments)).model_dump(mode="json", exclude_none=True)
        if action.tool == "report.publish" and self.MODE_EFFECT[arguments["mode"]] != action.effect_class:
            raise RuntimeFault("EFFECT_CLASS_MISMATCH")
        return arguments
