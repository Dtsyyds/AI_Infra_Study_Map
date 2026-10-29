from core.contracts.execution import Execution
from core.contracts.journal import ExecutionJournal
from core.contracts.task import TaskRequest
from core.errors import RuntimeFault


class ExecutionMiddleware:
    def __init__(self, journal: ExecutionJournal, max_steps: int = 4) -> None:
        self.journal = journal
        self.max_steps = max_steps

    async def before_step(self, execution: Execution, step_no: int) -> None:
        TaskRequest.model_validate(execution.request.model_dump())
        limit = execution.context["manifest"]["limits"]["max_steps"] if execution.request.task_type == "code_repair" else self.max_steps
        if step_no > limit:
            raise RuntimeFault("STEP_BUDGET_EXCEEDED")
        # 数据库时间同时检查租约、deadline、取消；本地计时不能替代该屏障。
        await self.journal.guard(execution)
