import json

from core.contracts.context import ContextProvider
from core.contracts.execution import Execution
from core.contracts.journal import ExecutionJournal
from core.contracts.tool import ToolInvoker
from core.hooks import Hooks
from core.middleware import ExecutionMiddleware
from core.nodes import NodeRegistry


class Kernel:
    def __init__(self, tools: ToolInvoker, context: ContextProvider, journal: ExecutionJournal,
                 registry: NodeRegistry | None = None, hooks: Hooks | None = None) -> None:
        self.tools = tools
        self.context = context
        self.journal = journal
        self.registry = registry or NodeRegistry()
        self.hooks = hooks or Hooks()
        self.middleware = ExecutionMiddleware(journal)

    async def run(self, execution: Execution) -> None:
        fields = {"task_id": execution.task_id, "attempt_id": execution.attempt_id}
        await self.hooks.emit("on_start", **fields)
        try:
            bundle = await self.context.build(execution)        # execution 携带当前这次执行的信息,包括任务 ID, Attempt ID, checkpoint 和上下文; context.build() 
            execution.context.update(bundle.model_dump())       # 更新执行上下文
            node = self.registry.resolve(execution.request.task_type)   # 按任务类型获取执行节点
            while True:
                step_no = execution.checkpoint + 1              # 下一步取决于已经提交的进度, 从 0 开始
                await self.middleware.before_step(execution, step_no)
                # Plan: 已持久化动作优先；恢复不能用参数相同来替代逻辑身份。
                action = await self.journal.pending_action(execution, step_no)
                if action is None:          # 先找旧动作, 如果没有持久化动作, 则调用节点计划下一步动作
                    action = node.plan(execution, step_no)      # 新动作由规划器生成随机 `tool_call_id`
                    if action is None:
                        await self.journal.finish(execution, execution.context["report"])
                        await self.hooks.emit("on_finish", **fields)
                        return
                    # Preparation Barrier：Ledger PREPARED 与 Action 同一次提交。
                    action = await self.journal.prepare(execution, action)      # `prepare()` 把动作和 `PREPARED` 账本记录一起保存
                await self.hooks.emit("on_step", **fields, step_no=step_no)     # 进程重启后，如果这个动作已经存在，就继续使用原动作、原 ID。**相同参数不能代替同一次逻辑调用的身份。**
                observation = await self.tools.invoke(action, execution)
                correlation = {}
                if action.tool.startswith("repair."):
                    correlation["candidate_no"] = action.arguments["candidate_no"]
                    identity = observation.get("record", {}).get("identity", {})
                    if identity.get("verification_run_id"):
                        correlation["verification_run_id"] = identity["verification_run_id"]
                await self.hooks.emit("on_tool", **fields, step_no=step_no,
                                      tool_call_id=action.tool_call_id, tool=action.tool, **correlation)
                # Observe 只取真实工具结果；控制元数据由核心赋值。
                key = {"document.fetch": "document", "document.check": "check",
                       "report.render": "report", "report.publish": "publication",
                       "repair.baseline": "baseline", "repair.generate": "candidate",
                       "repair.verify": "verification", "repair.report": "report"}[action.tool]
                context = self.context.observe(execution.context, {key: observation})
                artifact = None
                if action.tool == "report.render" or action.tool.startswith("repair."):
                    artifact = json.dumps(observation, ensure_ascii=False, indent=2).encode()
                # Checkpoint Barrier：结果/Artifact/会话/步骤一致提交。
                await self.journal.checkpoint(execution, action, observation, context, artifact)        # 在同一事务里保存步骤结果,账本完成状态,任务进度,会话快照,以及可选快照
                execution.context = context
                execution.checkpoint = step_no
                await self.hooks.emit("after_checkpoint", **fields, step_no=step_no)
        except BaseException:
            await self.hooks.emit("on_error", **fields)
            raise
