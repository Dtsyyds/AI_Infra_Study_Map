> 2026-10-24 更新：下文保留原 document_review 教学基线；新增实现与当前验证边界见 [修复阅读路径](repair_reading_path.md)、[验证器契约](verifier_contract.md)、[Badcase](badcases.md) 和根目录 TASK_STATUS.json。旧验收数字不代表本轮结果；Docker/真实模型仍 BLOCKED。

# 12 个文件的阅读顺序

第一次从 **`core/kernel.py`** 开始。先理解执行顺序和两个持久化屏障，再阅读具体基础设施。下面的小修改都是理解实验，应在本参考工程中做；不属于正式学习切片的完成进度。

| 顺序与文件 | 负责什么；先读入口 | 对应面试问题 | 可验证理解的小修改 |
|---|---|---|---|
| 1. `core/kernel.py` | `Kernel.run` 串起 Plan / Execute / Observe、prepare、checkpoint、finish | 进程崩溃后究竟从哪里恢复？ | 在测试 Journal 中记录调用顺序，证明 invoke 前一定出现 prepare |
| 2. `core/contracts/action.py` | `PreparedAction` 与 `EffectClass` 定义逻辑动作身份和副作用边界 | 参数相同为何不能作为重试身份？ | 改变已准备动作的参数但保留 tool_call_id，观察摘要/持久动作校验拒绝 |
| 3. `core/nodes.py` | `NodeRegistry.resolve` 与 `DeterministicPlanner.plan` 通过白名单重建执行节点 | 为什么不序列化 asyncio.Task 或闭包？ | 给未知 task_type 调用 Registry，检查它无法动态加载任意类 |
| 4. `api/main.py` | 从 `submit` 读到 `events / reconcile_task`，区分 HTTP 请求与 Durable Task | SSE 断线为什么不应该取消后台任务？ | 对 SSE 使用超前 Last-Event-ID，确认在响应流开始前收到 400 |
| 5. `runtime/storage/store.py` | 先看 `submit / claim`，再看 `_locked / prepare / dispatch / checkpoint / reconcile` | 如何证明认领与创建 Attempt 原子？ | 使用测试中的 INSERT 失败触发器，让真实数据库异常验证回滚 |
| 6. `runtime/orchestrator/worker.py` | `execute` 组织 Kernel、心跳与取消；`main_async` 管认领和遗留容器恢复 | 心跳断开时怎样防止旧 Worker 继续成功写回？ | 让旧 Attempt 租约过期，在新 Attempt 认领后尝试旧 checkpoint |
| 7. `runtime/tools/invoker.py` | `ToolRuntime.invoke` 的授权、缓存、派发和五类效果恢复分支 | 外部效果发生但响应丢失时该不该重试？ | 对同一 Action 分别使用 idempotent 与 unsafe 演示，比较 requests/effects |
| 8. `runtime/tools/mcp_gateway.py` | `describe / fetch` 使用真实 SDK Client、发现和 call_tool | 真实 MCP 协议验证与普通函数 Mock 有何不同？ | 修改资料字段并递增版本，再通过真实 stdio 调用检查返回值 |
| 9. `runtime/tools/docker_backend.py` | `command / check / cleanup_orphans` 固定沙箱约束、终止与归属检查 | 为什么杀 Docker CLI 不等于杀容器进程？ | Docker 可用后运行带启动屏障的 timeout 测试，确认容器真实消失 |
| 10. `runtime/context/engine.py` | `build / observe / apply_summary / validate_citations` 维护受保护事实 | 压缩上下文时哪些内容不能丢？ | 删除候选摘要中的金额或更改引用版本，观察明确拒绝 |
| 11. `runtime/security/policy.py` | `principal_from_token / authorize / admission_policy` 分别处理身份、工具权限与准入策略 | 为什么授权必须在缓存之前？ | 用 restricted 身份访问已有缓存，验证实际工具调用次数为 0 |
| 12. `runtime/observability/telemetry.py` | `TelemetryHook.emit / set_persistent_metrics` 区分 Trace、进程计数和 PG 聚合指标 | 为什么不能把 task_id 放进 Prometheus 标签？ | 创建多个 Task 后查询 /metrics，检查只有固定 status/tool/state 分类 |

读到事务表结构有疑问时查 `deploy/alembic/versions/0001_reference.py:SCHEMA`。读到工具参数有疑问时查 `runtime/tools/registry.py:ToolRegistry.validate`。读到身份泄露路径时查 `runtime/security/redaction.py`，它由调用前、审计和返回后三道边界共用。

每个实验先运行相关回归，再运行 `make check`。Docker 权限缺失时停在真实组件边界，保留 BLOCKED 结果，不把检查脚本搬到宿主运行后声称容器隔离已通过。完整结果以 `reports/<run_id>/acceptance.md` 为准。
