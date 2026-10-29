# 围绕本实例的 10 个追问

先遮住答案，尝试从数据库记录和源码解释每个问题。不要把这个教学实例包装为生产经历，也不要编造吞吐量、延迟、故障率或正式验收结果。

## 问题

1. API 返回 202 前究竟提交了什么？如果客户端超时后重发，为什么不会多创建一个 Task？
2. Worker 认领任务后，Attempt INSERT 真正失败，会留下 RUNNING 但没有 Attempt 的任务吗？如何用测试证明？
3. Preparation Barrier 和 Checkpoint Barrier 分别保护什么？为什么相同工具参数不能用来识别同一次逻辑调用？
4. Worker 崩溃后，哪些 Python 对象需要保存？恢复为什么不会重跑已完成 Step？
5. 外部服务已生效、响应还未写回时，五种 effect_class 分别如何处理？“查不到”为什么不等于“没执行”？
6. Lease、fence、取消意图和 deadline 各自解决什么问题？哪个机制能撤销已经发出的外部请求？
7. 为什么权限必须先于缓存？principal 从哪里来，审计不可用时如何处理新副作用？
8. 长上下文压缩后，如何证明审核依据没有被摘要丢掉？资料字段变化为什么应该改变结果？
9. Trace、Audit、Checkpoint、Artifact 和 Prometheus 指标有什么不同？为什么 SSE 不是历史事件回放？
10. 这个项目当前有哪些真实验证，哪些没有？怎样诚实描述 Docker 被阻塞的交付状态？

## 答案要点与代码证据

### 1. 持久受理与幂等

`api/main.py:submit` 等待 `Store.submit` 事务成功退出才返回。Task、idempotency_keys、Session、准入记录和接受审计一起提交。同一 principal/key 对请求内容摘要作比较；相同返回旧 Task，不同返回冲突。

幂等重放在 Session 和配额检查之前，因此旧请求不会因自己的活动会话而被拒绝。受理幂等不能自动推广为外部服务幂等，后者还需要稳定 tool_call_id 和依赖服务的去重支持。

证据：`tests/integration/test_storage.py:test_submit_idempotency_and_session_atomic`，演示 B 的 Task ID 与数据库逻辑任务数量。

### 2. Claim 原子性

`Store.claim` 在一个连接事务内锁定 Task、更新 RUNNING/fence/lease、INSERT Attempt。测试通过真实数据库触发器使 INSERT 失败，随后检查 Task 仍未被认领、没有 Attempt。两个分别提交的 Repository 方法无法提供这个保证。

证据：`test_claim_attempt_insert_failure_rolls_back`。仅 mock 一个方法抛异常，不能证明 PostgreSQL 已经回滚了此前状态更新。

### 3. 两个屏障与动作身份

Preparation 保存 Action 和 PREPARED Ledger，确定工具、已校验参数、副作用类型和随机 tool_call_id；派发前还要提交 Audit + DISPATCHED。Checkpoint 一次提交 Observation、Step、Ledger COMMITTED、Session 快照、Task 恢复位置和 Artifact。

参数相同可能是用户要求执行两次，也可能是一次动作的重试。逻辑身份必须由保存的 tool_call_id 决定。`pending_action` 优先恢复原动作，不能重新规划后用参数碰巧一致来冒充原操作。

证据：`Kernel.run`、`Store.prepare / dispatch / checkpoint`、`ToolRegistry.validate`。

### 4. 恢复的是数据，不是活执行对象

数据库保存白名单类型、JSON 参数和结果、状态、时间、版本。asyncio.Task、Lock、Event、线程池和闭包都只属于当前进程，不序列化。

新 Worker 从 Task checkpoint、原 Action 与 Session 快照重建 Execution；`NodeRegistry` 重建执行节点，`ContextEngine.build` 重建上下文，从 checkpoint + 1 继续。Task ID 保持不变，Attempt ID 和 fence 改变。

证据：`worker.main_async / execute`、`core/contracts/execution.py`、演示 C 的 checkpoint 屏障和实际 MCP 读取次数。Docker 被阻塞时只能声称恢复位置得到验证，不能声称最终审核完成。

### 5. 副作用边界

- READ_ONLY 可安全再次读取，但仍要授权、校验和记录。
- LOCAL_TRANSACTIONAL 的本地效果与 Ledger / checkpoint 在同一 PostgreSQL 事务提交。
- EXTERNAL_IDEMPOTENT 重发使用原 tool_call_id，依赖服务去重；多请求可以对应单效果。
- EXTERNAL_QUERYABLE 对已有派发先做权威查询；肯定 applied 可以确认，临时 404/超时不能推出原请求永远不会生效。
- EXTERNAL_UNSAFE 无法安全确认时进入 UNKNOWN / NEEDS_REVIEW，禁止自动重发。

证据：`ToolRuntime.invoke / _publish / _query`、独立 `business_service.publish`、`arc_business.requests / effects`。Runtime 和业务服务没有共享一次提交，故障窗口是真实存在的。

### 6. 四种控制各司其职

Lease 是活 Worker 的时间窗口；fence 和 current_attempt 防止旧 Worker 在失去所有权后写回。取消是持久意图；deadline 从提交开始消耗预算，包括排队与恢复。所有持久判断使用拿到锁之后的数据库时间。

这些机制都不能撤销已经发出去的外部请求。取消和超时之后仍有不确定效果时必须保留 NEEDS_REVIEW，不能用 CANCELLED/TIMED_OUT 掩盖。运维对账也不能重置原 deadline。

证据：`Store._locked / fail / reconcile`、`worker.execute`、`test_cancel_before_success_and_success_before_cancel`、`test_deadline_does_not_mask_dispatched_external_effect`。

### 7. 缓存与安全

缓存命中也在释放受保护数据，因此必须先验证身份和静态授权。principal 从验证过的演示凭据取得，不从 Planner 或工具参数取得。缓存键包含 principal/scope、资料版本和工具版本，副作用确认不进入缓存。

新的副作用派发前，`Store.dispatch` 在事务里写必要审计，失败就回滚且不调用工具。数据库不可用时无法保证记录拒绝审计，这是观测缺口，不能伪造“全量审计已保存”。

证据：`policy.principal_from_token / authorize`、`ToolRuntime.invoke`、`test_authorization_precedes_cache_and_real_mcp_call_count`、`test_audit_database_failure_prevents_independent_service_request`。

### 8. 上下文与业务事实

规则 ID、版本、source 与文档金额/附件等关键事实独立放在 Protected Facts，不依赖自然语言摘要。确定性压缩移除旧工作记忆，`apply_summary` 比较保护内容，缺字段或改值就拒绝。estimated_tokens 只是字符估算。

固定检查程序比较 amount 与 threshold、检查 invoice/approval 附件，不根据 document_id 硬编码结论。修改资料时递增版本，既反映新业务事实，也让只读缓存失效。规则引用还要通过 `validate_citations` 验证。

证据：`ContextEngine`、`runtime/tools/docker_backend.py:CHECK_SCRIPT`、`tests/unit/test_context.py`。检查脚本的纯逻辑测试不能替代真实 Docker 隔离验证。

### 9. 观测与恢复不可混为一谈

Checkpoint 和 Ledger 是恢复依据；Artifact 是与 checkpoint 一致提交的 BYTEA 结果；Audit 是追加的行为证据；Trace 是故障分析日志，普通遥测失败不改变已提交结果。DB 超级用户仍可绕过审计表保护。

Prometheus 只使用固定 status/tool/state 分类，Task ID 等高基数字段放 Trace。API 和 Worker 不共享内存，因此 API 的持久指标来自 `Store.metrics` 的 PostgreSQL 一致聚合。

SSE 每秒提供最新持久快照。版本没变不重复发；跳跃时发送最新完整状态并标记 reconciled，不承诺逐事件重放。断流默认不取消 Task。

证据：`telemetry.set_persistent_metrics`、`Store.diagnostics`、`api.main.events`、`Hooks.emit`。

### 10. 如实表述当前交付

可以描述实际跑过的 Python 3.11.17、PostgreSQL 17.11 事务、MCP 2.2.0 stdio、单元检查和独立进程故障；必须附本次报告里的实际状态和 Task ID。Docker socket 权限阻塞意味着真实容器限制、终止和完整标准审核链路还没有通过本轮验证。

不能把 skipped / blocked 计入 passed，不能说“实例完整跑通”“生产级可靠”“正式 W8 已过”。完整 270 次压测和全部 10/30 次重复实验尚未完成。本项目不修改、不复用正式学习工程源码，也不计入其切片进度。

证据：`reports/<run_id>/environment.json`、`commands.log`、`test-results.*`、`demo-results.json`、`acceptance.md`。性能与生产经历不在证据范围内。
