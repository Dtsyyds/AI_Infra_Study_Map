from datetime import UTC, datetime, timedelta

from core.contracts.execution import Execution
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.kernel import Kernel
from runtime.context.repair_context import RepairContext
from runtime.repair.manifest import load_case
from tests.unit.test_kernel import Journal


async def test_three_candidate_loop_uses_new_calls_and_separate_outcome():
    manifest, files = load_case("case-01")
    ex = Execution(
        task_id="task",
        attempt_id="attempt",
        fence=1,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        principal=Principal(principal_id="demo-reviewer"),
        request=TaskRequest(task_type="code_repair", case_id="case-01", session_id="s"),
        context={"manifest": manifest.model_dump(), "base_files": files},
    )
    journal = Journal()
    actions = []

    class Tools:
        async def invoke(self, action, execution):
            actions.append(action)
            n = action.arguments["candidate_no"]
            if action.tool == "repair.baseline":
                return {"verdict": {"status": "FAIL"}}
            if action.tool == "repair.generate":
                return {"candidate_no": n, "candidate_hash": str(n) * 64}
            if action.tool == "repair.verify":
                return {"record": {"identity": {"candidate_no": n}}, "verdict": {"status": "FAIL"}}
            return {"business_outcome": "UNRESOLVED", "gate_verdict": "FAIL"}

    await Kernel(Tools(), RepairContext(), journal).run(ex)
    assert [a.arguments["candidate_no"] for a in actions if a.tool == "repair.generate"] == [
        1,
        2,
        3,
    ]
    assert len({a.tool_call_id for a in actions}) == 8
    assert ex.checkpoint == 7 and journal.result["business_outcome"] == "UNRESOLVED"
