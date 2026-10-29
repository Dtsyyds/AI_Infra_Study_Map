> 2026-10-26 当前更新：Docker已恢复可访问，P0、独立Harness、容器矩阵和修复恢复均已有真实证据。下文早期验证边界作为历史说明保留；当前权威状态见根目录TASK_STATUS.json及新delivery报告。真实模型仍未授权，不能以Fake通过代替。

> 2026-10-24 更新：下文保留原 document_review 教学基线；新增实现与当前验证边界见 [修复阅读路径](repair_reading_path.md)、[验证器契约](verifier_contract.md)、[Badcase](badcases.md) 和根目录 TASK_STATUS.json。旧验收数字不代表本轮结果；Docker/真实模型仍 BLOCKED。

# 六层教学架构与持久边界

Agent Runtime Core Reference 用一个虚构采购资料审核流程，展示持久受理、工具执行、恢复和不确定副作用。它是教学参考实现；生成了代码、通过了部分真实测试，都不能据此认定为生产平台或可靠性已经充分验证。正式学习工程和 month01–month04 不属于本项目修改范围。

## 进程与依赖

```text
本地客户端
   | HTTP / Idempotency-Key / SSE
   v
FastAPI 127.0.0.1:18081                   模拟业务依赖 127.0.0.1:18082
   | 持久受理、查询、取消、运维对账                ^ 独立HTTP请求 / 独立事务
   v                                            |
PostgreSQL 127.0.0.1:55439 <--- 独立可信 Worker ----+
   | arc_ref.*                    |
   | arc_business.*               +-- L1 Kernel / 确定性 Planner
   |                              +-- L4 ContextEngine
   |                              +-- L5 静态策略 / L6 Trace
   |                              +-- L2 ToolRuntime
   |                                    |
   +-- 规则、文档 <--- MCP stdio 子进程 ---+
                                        |
                                固定 Docker 检查容器
                                无网络 / 非 root / 只读根目录
                                不接收 DB 凭据或 Docker socket
```

API、Worker、MCP Server 和模拟业务服务在独立进程中运行。模拟业务服务是故障测试依赖：虽然使用同一个 PostgreSQL 实例，但业务效果在 `arc_business` schema，以独立连接、独立事务提交；它不能和 Runtime Ledger 一次提交。Docker 是工具隔离边界，不是受信 Worker 本身。

2026-10-26已在有效Docker组会话获得真实容器执行、资源异常、终止与归属清理证据，P0完整审核链路通过。当前结果以 `reports/<run_id>/FINAL_REPORT.md` 和测试结果为准；PG/MCP历史局部证据仍不能替代真实容器记录。

## 六层职责与真实实现

| 层 | 主要源码 | 负责什么 | 不负责什么 |
|---|---|---|---|
| L1 Kernel | `core/kernel.py:Kernel.run`、`core/nodes.py:DeterministicPlanner` | Plan / Execute / Observe；调用 Preparation 与 Checkpoint 屏障；白名单节点重建 | 不建立 DB 连接，不绕过工具运行时 |
| L2 Tool Runtime | `runtime/tools/invoker.py:ToolRuntime.invoke` | 授权、Schema、缓存、Ledger、MCP、Docker、外部效果分类 | 不把缓存当执行确认，不宣称外部请求与本地提交原子 |
| L3 Orchestrator | `runtime/orchestrator/worker.py:execute`、`runtime/storage/store.py:Store` | 认领、Attempt、租约、fencing、状态变更、取消、deadline、reconcile | 不序列化协程、锁、Event 或正在运行的容器 |
| L4 Context | `runtime/context/engine.py:ContextEngine` | 检索规则、保留关键事实、压缩工作记忆、快照重建、引用验证 | 不依赖摘要模型，不声称字符估算是 tokenizer 精确值 |
| L5 Policy | `runtime/security/policy.py`、`runtime/security/redaction.py` | 验证本地身份、静态授权、定义准入限额、三道脱敏 | 不提供 SSO、RBAC 产品或强多租户沙箱 |
| L6 Observability | `runtime/observability/telemetry.py`、`Store.diagnostics` | 关联 Trace、低基数指标、追加审计、只读诊断 | Trace 不是 Checkpoint；审计不能对抗 DB 超级用户 |

`core/contracts` 仅含 DTO、枚举和 Protocol。`core` 只依赖这些接口和自己的实现；具体模块依赖 `core`，组装入口是 Worker。`tests/unit/test_contracts.py:test_ast_dependency_direction` 使用 AST 检查 `core` 的导入，拒绝导入 `runtime` 或 `api`。Alembic 只负责迁移；运行时统一用 psycopg 异步连接和显式 SQL 事务，没有混用 ORM 的工作单元。

## 事务边界

| 事务 | 同时提交的事实 | 提交失败后 |
|---|---|---|
| `Store.submit` | Task、幂等键、Session、准入计数和接受审计 | 不返回 202，不留下第二个逻辑任务 |
| `Store.claim` | Task 的 RUNNING / fence / lease、Attempt INSERT、认领审计 | Attempt INSERT 真正失败时，Task 认领更新也回滚 |
| `Store.prepare` | 原始 PreparedAction 与 Ledger PREPARED | 没有动作可以合法派发 |
| `Store.dispatch` | Ledger DISPATCHED / 派发次数与必要审计 | 审计写入失败时不派发新副作用 |
| `Store.checkpoint` | Step、Ledger COMMITTED、Task checkpoint、Session 快照与版本、可选 Artifact、本地效果 | 任意失败全部回滚；不能出现有 Artifact 而无对应 checkpoint |
| `Store.finish` / `fail` | 受控终态及审计 | 旧租约或不满足状态条件的写回被 DB 拒绝 |
| `Store.reconcile` | Ledger 人工确认、证据摘要、Task 版本 CAS、运维审计 | 版本冲突或仍有阻塞条件时不悄悄重新排队 |

这些边界不是分别提交的 Repository 方法拼接。连接上下文退出之后才算完成提交。API 的 202 只代表任务已持久受理，不能解释为审核已完成。

`tool_call_id` 是 Plan 时生成的随机 ID，在 `prepare` 中成为持久逻辑身份。新 Attempt 继续读取原 Action，保留 `task_id / step_no / tool_call_id`；参数相同并不能证明是同一个逻辑调用。

## 持久事实归属

- `arc_ref.tasks / attempts / steps / actions / ledger`：任务状态与恢复位置。
- `arc_ref.sessions`：Session 版本与上下文快照；同一 principal/session 只能有一个活动任务，NEEDS_REVIEW 也占用会话。
- `arc_ref.rules / documents`：虚构的长期规则和业务资料，带稳定 ID、版本和来源片段。修改业务资料时同时递增版本，保证缓存失效与引用语义一致。
- `arc_ref.artifacts`：BYTEA 小型报告，单件 2 MiB、单 Task 8 MiB、平台逻辑总量 512 MiB；`artifact_quota` 锁保护总配额检查。
- `arc_ref.tool_cache`：只读结果缓存；不存外部副作用确认，不承担恢复事实源职责。
- `arc_ref.audit`：追加式审计；UPDATE / DELETE 触发器拒绝普通修改。拥有足够数据库管理权限的用户仍可能禁用保护。
- `arc_business.requests / effects`：模拟业务服务自己的请求次数与效果次数，必须分别统计。
- `reports/`：执行结果导出，仅用于阅读证据；Runtime 不从报告目录恢复。

持久时间均为 UTC，租约和 deadline 使用取得行锁后的数据库时间。`monotonic` 或活进程 timeout 只能用于耗时控制，不能替代 DB 对写回的授权。

## 三种结果不能混淆

`SUCCEEDED + review_decision=NEEDS_DOCUMENTS` 表示流程成功发现材料缺失。`PASS` 表示材料符合教学规则。`NEEDS_REVIEW + Ledger UNKNOWN` 表示外部效果不确定，运维需要对账；这与发票或审批材料缺失无关。

本项目没有 Redis、Outbox、消息队列、通用工作流引擎、新前端或第二套 Agent 框架。确定性 Planner 选择工具顺序，业务结果来自 MCP 实际资料、数据库规则和固定检查程序，不具备真实模型推理能力。
