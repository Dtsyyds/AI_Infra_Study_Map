> 2026-10-24 更新：下文保留原 document_review 教学基线；新增实现与当前验证边界见 [修复阅读路径](repair_reading_path.md)、[验证器契约](verifier_contract.md)、[Badcase](badcases.md) 和根目录 TASK_STATUS.json。旧验收数字不代表本轮结果；Docker/真实模型仍 BLOCKED。

# 沿着一个任务读源码

建议先读 `core/kernel.py:Kernel.run`，暂时把它依赖的三个接口当作黑盒。随后按下面顺序往外展开。完整参考路径是 `DOC-001 → document.fetch → document.check → report.render → 可选 report.publish`；没有发布时共三步。

本轮真实 Docker 执行被环境权限阻塞。下面说明的是已实现的执行路径；哪些段实际运行成功，应对照 `reports/<run_id>/demo-results.json` 和 `acceptance.md`。A 的最终报告不能在 Docker 被阻塞时声称已经得到验证。

## 准备观察窗口

在本工程目录运行：

```bash
make db-start
make start
make demo
```

`make demo` 通过 API 提交并导出各场景的 Task ID 与检查结果。拿到 ID 后，使用项目自己的演示凭据查询；以下的 `TASK_ID` 是需要替换的占位符，`ARC_USER_TOKEN` 应取自本项目配置，不要使用已有生产凭据：

```bash
curl --silent --header "Authorization: Bearer $ARC_USER_TOKEN" \
  http://127.0.0.1:18081/tasks/TASK_ID
curl --silent --header "Authorization: Bearer $ARC_USER_TOKEN" \
  http://127.0.0.1:18081/tasks/TASK_ID/diagnostics
curl --no-buffer --header "Authorization: Bearer $ARC_USER_TOKEN" \
  http://127.0.0.1:18081/tasks/TASK_ID/events
curl --silent http://127.0.0.1:18081/metrics
```

diagnostics 是一致只读数据库快照，包含 Task、Attempt、Step、Action、Ledger、Audit 和 Artifact 元数据。它不是根据日志拼接出的推断结果。

报告已提交时，从 diagnostics 的 artifacts 取出 artifact_id，再访问 `GET /tasks/{task_id}/artifacts/{artifact_id}` 下载 BYTEA 内容。`make start` 只启动 API 和模拟业务依赖；演示自行创建独立 Worker。手工观察持续认领时另开终端运行 `make worker`，演示期间不要额外运行它，以免争抢故障场景的指定任务。

## 1. 请求进入：先分清三个对象

读 `core/contracts/identity.py:RequestContext`、`core/contracts/task.py:TaskRequest` 和 `core/contracts/execution.py:Execution`。

- RequestContext 是一次 HTTP 请求的 principal 和 request_id。
- Task 是持续存在的逻辑任务，HTTP 断线不会删除它。
- Execution 携带某一次 Attempt 的 fence、deadline、checkpoint 和 Session 版本，用于受控写回。

`api/main.py:request_context` 从 Bearer 凭据取得身份，`runtime/security/policy.py:principal_from_token` 使用恒时比较。工具参数不能设置 principal。`TaskRequest` 拒绝额外字段，`budget_ms` 只能是 1000–1800000 的整数；绝对 deadline 不由客户端传入。

观察：对非法输入，API 在 Task 创建之前返回验证错误；它不会创造一个假的 Attempt 来记录输入校验失败。

## 2. 受理：先提交，再返回 202

读 `api/main.py:submit` 和 `Store.submit`。同一 principal 的准入行先被锁定，然后优先查幂等键。相同键、相同请求返回原 Task；相同键、不同请求返回 409。只有没有幂等重放时，才检查 Session 冲突、活动配额和速率限制。

新的 Task、幂等键、Session 和接受审计同事务提交。`deadline_at = database_now + budget_ms`，因此排队时间也花费预算。

观察：演示 B 记录两次返回的同一个 `task_id`、一次 409，以及数据库逻辑任务数；diagnostics 此时有 Task，没有执行 Step。原任务仍活动时重放幂等键，不应误报 SESSION_BUSY。

## 3. 认领：Task 与 Attempt 原子绑定

读 `runtime/orchestrator/worker.py:main_async` 和 `Store.claim`。Worker 用 PostgreSQL 行锁和 `SKIP LOCKED` 竞争 Task。更新 RUNNING、递增 fence、写 lease 和 INSERT Attempt 在同一事务中。

到期或已取消的任务先处理，尚无执行资格时不创建 Attempt。已有未确认外部效果会转 NEEDS_REVIEW，不能用 TIMED_OUT 掩盖风险。数据库测试会安装临时失败触发器，让 Attempt INSERT 真正失败，再检查整个认领回滚。

观察：diagnostics 的 `attempts` 与 Task 的 `current_attempt / fence / lease_until`。同一个 Task 恢复后可有多个 Attempt，已提交 Step 仍只有一个逻辑记录。

## 4. 重建上下文与 Plan

`ContextEngine.build` 检查 Session 版本，从 checkpoint 重建工作记忆、摘要和 Protected Facts，并通过 `Store.rules` 获取数据库规则。恢复继续使用快照中已选定的规则版本，不能悄悄变更审核依据。

`NodeRegistry.resolve` 只接受白名单 `document_review`。`DeterministicPlanner.plan` 根据 step_no 选择固定工具，生成结构化 `PreparedAction`。它不生成整份审核答案；金额、附件和最终判定取自实际工具输入输出。

观察：Action 的 `tool / effect_class / arguments_digest`；规则 ID 是 `RULE-APPROVAL` 与 `RULE-INVOICE`，版本和 source 进入后续引用。

## 5. Preparation Barrier：动作先持久化

`Kernel.run` 先查询 `ExecutionJournal.pending_action`。存在未完成动作时直接恢复它；不存在才调用 Planner。随后显式调用 `ExecutionJournal.prepare`，由 `Store.prepare` 同事务写 Action 和 Ledger PREPARED。

从这个提交点以后，逻辑工具 ID 已经固定。重启不能因为“参数看起来一样”而另造一次工具调用。Middleware 在每步前检查输入、步数预算、数据库租约、取消与 deadline；普通 Hook 没有工具执行接口。

观察：diagnostics 可以看到 PREPARED Action，即使还没有实际派发和 Step 结果。

## 6. Execute：授权、审计和真实工具

读 `ToolRuntime.invoke`。顺序是静态授权、Schema 与参数摘要验证、数据库 guard、账本恢复分支、可选只读缓存、持久派发屏障、实际执行。

`document.fetch` 使用 `MCPDocumentGateway` 启动独立 stdio Server，真实完成初始化、工具发现和 `get_document` 调用。Server 从 PostgreSQL 只读事务返回资料。缓存按 principal、scope、资料版本、工具版本区分；权限检查始终在缓存前面。

`document.check` 将资料和规则传给固定 Docker 脚本。非 root、无网络、只读根、CPU/内存/PID/墙钟限制在 `DockerBackend.command / check` 中明确设置。容器不接收平台凭据。Docker 不可用时抛明确错误，不在宿主执行脚本冒充隔离验证。

`report.render` 验证规则引用并构造虚构教学报告；本地持久效果在下一 checkpoint 中提交。`report.publish` 使用独立 HTTP 请求，幂等键是稳定 tool_call_id，外部效果类别决定恢复分支。

观察：Ledger 的 DISPATCHED 与 `dispatch_count` 代表 Runtime 记录的派发；外部实际效果要查询模拟业务服务自己的表。两者不是同一个统计。

## 7. Observe：工具结果进入受控上下文

`Kernel.run` 根据白名单工具把实际 observation 放入 `document / check / report / publication`。`ContextEngine.observe` 拒绝工具覆盖规则、Session 版本等控制字段，完整保留 document 受保护事实。生成 report 时检查每条引用的 rule_id、version 和 source。

长历史压缩只处理工作记忆，关键事实独立保存。`estimated_tokens` 是字符数除以 4 的估算，不是 tokenizer 结果。`apply_summary` 对故意丢失事实的候选摘要抛错；预算放不下关键事实时明确拒绝，不静默截断。

观察：Task `context.protected_facts`、`working_memory`、`summary`、`session_version`。

## 8. Checkpoint Barrier：结果与恢复位置一致

Kernel 显式调用 `ExecutionJournal.checkpoint`。Store 再次检查租约、fence、取消、deadline、持久 Action 身份和 Session 版本。Step、Ledger COMMITTED、Task checkpoint、Session 快照、可选 Artifact 与本地效果同事务提交。

报告 Artifact 是 PostgreSQL BYTEA。配额失败时整个 checkpoint 回滚，不能把截断的报告宣称为完整结果。

观察：同一个 step_no 对应 Step、Ledger 和 Artifact 元数据；`checkpoint` 是最后完成的步号，下一次从 `checkpoint + 1` 执行。演示 C 的精确屏障位于 checkpoint 已提交之后、下一次 Plan 之前。

## 9. 终结、SSE 与取消

Planner 返回无下一动作时，`Store.finish` 经过受控事务把 Task 标记为 SUCCEEDED。审核结论 NEEDS_DOCUMENTS 仍然可以是成功任务；它说明材料不足，不代表外部副作用 UNKNOWN。

SSE 首次发送最新完整快照，之后每秒读取持久版本；版本不变不重发，跳跃时只发送最新快照并标记 reconciled。非法或超前 Last-Event-ID 在流开始前返回 400。断开 SSE 只结束观察，不设置取消意图。

显式取消通过 `Store.cancel` 持久设置意图，Worker 在 guard 或心跳发现后停止后续动作。已提交的报告、已发生的外部效果不会因此回滚。结束观察后运行 `make stop`，需要停止数据库时运行 `make db-stop`；两者保留数据，不使用全局清理命令。
