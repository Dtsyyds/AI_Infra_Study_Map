"""白名单重建执行节点；确定性规划器只选择工具，答案取自 Observation。"""
import hashlib
import json
from typing import Any, Protocol
from uuid import uuid4

from core.contracts.action import EffectClass, PreparedAction
from core.contracts.execution import Execution
from core.errors import RuntimeFault


class ExecutionNode(Protocol):
    def plan(self, execution: Execution, step_no: int) -> PreparedAction | None: ...


class DeterministicPlanner:
    def plan(self, execution: Execution, step_no: int) -> PreparedAction | None:
        ctx = execution.context
        effect = EffectClass.READ_ONLY
        arguments: dict[str, Any]
        if step_no == 0:
            tool, arguments = "document.fetch", {"document_id": execution.request.document_id}
        elif step_no == 1:
            tool = "document.check"
            arguments = {"document": ctx["document"], "rules": ctx["rules"]}
        elif step_no == 2:
            tool = "report.render"
            arguments = {"check": ctx["check"], "rules": ctx["rules"]}
            effect = EffectClass.LOCAL_TRANSACTIONAL
        elif step_no == 3 and execution.request.publish_mode != "none":
            tool = "report.publish"
            arguments = {"report": ctx["report"], "mode": execution.request.publish_mode}
            effect = {"idempotent": EffectClass.EXTERNAL_IDEMPOTENT,
                      "unsafe": EffectClass.EXTERNAL_UNSAFE,
                      "queryable": EffectClass.EXTERNAL_QUERYABLE}[execution.request.publish_mode]
        else:
            return None
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return PreparedAction(task_id=execution.task_id, step_no=step_no,
                              tool_call_id=str(uuid4()), tool=tool, arguments=arguments,
                              arguments_digest=hashlib.sha256(encoded.encode()).hexdigest(),
                              effect_class=effect)


class RepairPlanner:
    """Finite control flow. Model generation occurs only after persisted dispatch."""
    def plan(self, execution: Execution, step_no: int) -> PreparedAction | None:
        ctx = execution.context
        if "report" in ctx:
            return None
        candidate = ctx.get("candidate", {})
        verification = ctx.get("verification", {})
        number = candidate.get("candidate_no", 0)
        maximum = ctx["manifest"]["limits"]["candidates"] if execution.request.repair_strategy == "loop" else 1
        if "baseline" not in ctx:
            tool, args = "repair.baseline", {"candidate_no": 0}
        elif number == 0:
            tool, args = "repair.generate", {"candidate_no": 1}
        elif verification.get("record", {}).get("identity", {}).get("candidate_no") != number:
            tool, args = "repair.verify", {"candidate_no": number}
        elif verification["verdict"]["status"] == "FAIL" and number < maximum:
            tool, args = "repair.generate", {"candidate_no": number+1}
        else:
            tool, args = "repair.report", {"candidate_no": number}
        encoded = json.dumps(args,sort_keys=True,separators=(",", ":"))
        return PreparedAction(task_id=execution.task_id, step_no=step_no, tool_call_id=str(uuid4()),
                              tool=tool,arguments=args,arguments_digest=hashlib.sha256(encoded.encode()).hexdigest(),
                              effect_class=EffectClass.READ_ONLY)


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, ExecutionNode] = {"document_review": DeterministicPlanner(), "code_repair": RepairPlanner()}

    def resolve(self, task_type: str) -> ExecutionNode:
        if task_type not in self._nodes:
            raise RuntimeFault("UNKNOWN_TASK_TYPE")
        return self._nodes[task_type]
