# Agent Runtime Core Reference 源码跟读学习记录

记录日期：2026 年 10 月 24 日。学习范围：本次连续对话中的项目源码跟读、场景问答及纠错。

本次完成第一遍核心执行链路与代表性故障测试的讲解。学习方式为阅读源码、追踪调用链和回答场景题；没有在本次跟读中重新启动服务或执行测试。完成讲解不等于已完成独立实践或运行验收。

## 一 学习进度

| 内容 | 当前状态 |
|---|---|
| 项目定位与核心架构 | 已讲解 |
| Kernel、Planner、动作身份与副作用分类 | 已讲解 |
| 提交、认领、派发、checkpoint、结束事务 | 已讲解 |
| Worker 租约、心跳、fence 与接管恢复 | 已讲解 |
| 取消、超时、不确定效果与人工对账 | 已讲解 |
| API 幂等、Session 排他、SSE 与报告下载 | 已讲解 |
| 上下文恢复、规则版本、受保护事实与压缩 | 已讲解 |
| MCP 资料获取、缓存与 Docker 检查 | 已讲解核心路径 |
| 身份、工具授权、脱敏、Hooks、审计与指标 | 已讲解 |
| 外部业务服务幂等与代表性故障测试 | 已讲解 |
| 综合判断、独立复述和运行练习 | 待继续巩固 |

项目的 12 个推荐阅读文件已覆盖核心职责，但并非逐行精读所有源码和测试。后续重点是应用和验证，不再以讲过多少文件作为掌握程度。

## 二 项目与业务主线

项目是虚构采购资料审核的教学参考实现。确定性规划器按步骤选择工具，没有真实模型推理。

```text
提交任务
  → Worker 认领
  → document.fetch 读取资料
  → document.check 检查金额与附件
  → report.render 生成 JSON 报告
  → 可选 report.publish 发布报告
  → finish 提交任务成功
```

业务规则包含：金额严格大于 10000 时需要审批附件，所有资料需要发票。因此金额恰好为 10000、已有发票且没有审批附件，可以得到 PASS。

运行状态与业务结论不同：`SUCCEEDED + NEEDS_DOCUMENTS` 表示成功完成审核，并发现材料缺失。`DOCKER_UNAVAILABLE` 表示检查基础设施不可用，不能伪装成业务审核结论。

## 三 核心执行与持久边界

```text
API submit
  → Task、幂等键、Session、准入计数、受理审计一起提交
Worker claim
  → Task 执行身份、租约、Attempt、认领审计一起提交
Kernel run
  → 从 checkpoint + 1 开始
  → 优先读取已保存动作，没有才规划
  → prepare 保存 Action 和 PREPARED 账本
  → ToolRuntime 授权、校验、恢复判断、缓存或实际派发
  → observe 将结果合入候选上下文
  → checkpoint 原子保存结果和恢复进度
  → 无下一动作时调用 finish
```

| 状态或操作 | 可以证明什么 | 不能证明什么 |
|---|---|---|
| PREPARED | 动作身份和参数已持久保存 | 工具已经执行 |
| DISPATCHED | 派发记录已提交 | 请求一定发出、外部一定生效 |
| 工具返回成功 | 本次调用获得结果 | checkpoint 已提交 |
| COMMITTED 与 checkpoint | 步骤结果和恢复位置已提交 | 整个 Task 已成功结束 |
| SUCCEEDED | 任务成功终态已提交 | 业务资料必然合格 |

checkpoint 会将 Step、Ledger、Task 进度与上下文、Session 快照、可选 Artifact、本地事务型效果及提交审计放入同一事务。数据库提交后才推进内存进度。

## 四 身份 租约与时间预算

| 字段 | 含义 | 恢复时的变化 |
|---|---|---|
| task_id | 整个逻辑任务 | 不变 |
| attempt_id | 一次认领执行 | 新建 |
| fence | 执行代数 | 接管时递增 |
| tool_call_id | 已准备动作的逻辑调用身份 | 保持原值 |
| checkpoint | 最后完成提交的步骤编号 | 从数据库恢复 |
| lease_until | 当前 Attempt 的执行资格期限 | 有效租约内可以续租 |
| deadline_at | 整个任务的截止时间 | 接管和对账都不重置 |

身份匹配只说明是原认领者。提交还要求租约有效，即使没有新 Worker 接管，旧 Worker 的租约到期后也不能提交或自行续租。

任务预算从受理时开始计算，排队也消耗预算。旧 Worker 失去资格后，数据库通过状态、Attempt、fence 和租约检查拒绝受控写入。取消本地协程不能撤销已经发出的外部请求。

## 五 外部效果与人工对账

外部服务与 Runtime 使用独立事务。外部已经生效，但响应或 checkpoint 尚未保存时，本地仍可能只看到 DISPATCHED。

| 副作用类别 | 未完成步骤的主要恢复策略 |
|---|---|
| READ_ONLY | 可以重新读取，可能命中缓存 |
| LOCAL_TRANSACTIONAL | 可以重新计算，持久效果与 checkpoint 同事务提交 |
| EXTERNAL_IDEMPOTENT | 崩溃接管路径可用原键重发，由接收端去重 |
| EXTERNAL_QUERYABLE | 查询明确的已生效证据，否则转人工对账 |
| EXTERNAL_UNSAFE | 不自动重发，转 UNKNOWN 与 NEEDS_REVIEW |

幂等 HTTP 错误在当前项目中没有自动重试调度；不能把错误名 RETRYABLE 理解为一定会自动重试。

取消意图与外部事实必须分开：用户明确取消，并不能证明此前发布没有生效。存在未知效果时优先 NEEDS_REVIEW。

对账的三个结论为 CONFIRMED_APPLIED、CONFIRMED_NOT_APPLIED 和 INCONCLUSIVE。对账需要独立运维身份、当前任务版本和证据；代码检查格式与版本，但不自动判断证据真实性。

```text
对账后仍有未知效果 → NEEDS_REVIEW
没有未知效果且已取消 → CANCELLED
没有取消但 deadline 已过 → TIMED_OUT
以上均不成立 → QUEUED，等待认领
```

确认已生效后恢复会复用对账结果；确认未生效且仍允许执行时，才可再次派发。Task 为 CANCELLED 不代表外部效果从未发生。

## 六 API 幂等与观察接口

客户端提交幂等以 `(principal_id, Idempotency-Key)` 为范围：

- 同键、同请求：返回原 Task 的当前记录，不重置 deadline。
- 同键、不同请求：返回 409 IDEMPOTENCY_CONFLICT。
- 新键：进入新任务准入检查，不保证能创建任务。同一 Session 已有活动任务时返回 SESSION_BUSY。

幂等重放检查先于 Session、活动配额和速率检查。Task 与幂等键映射同事务保存，避免响应丢失后的重试创建重复任务。

SSE 每秒轮询持久快照，版本不变不重复发送；每次建立连接先发送最新快照。断线重连不会补发全部中间版本，版本跳跃时设置 `reconciled=true`。这里表示快照同步，与人工对账不同。

断开 SSE 只结束观察连接，不写取消标记。下载报告读取已提交 Artifact，不重新执行审核；报告存在也不代表整个任务已成功。

## 七 上下文 工具与安全

### 上下文

恢复优先使用 checkpoint 保存的规则版本，避免同一任务中途改变审核依据。规则和资料进入 protected_facts，旧观察可从 working_memory 移除，但不能用一句摘要替代金额、附件和规则。

当前 summary 是确定性压缩说明与历史指纹，没有调用模型总结。清空历史后仍超出字符预算会抛出 CONTEXT_LIMIT。estimated_tokens 只是字符数估算。

### MCP 与缓存

Worker 通过独立 MCP 子进程读取 PostgreSQL 资料。网关只接受固定的 get_document 工具集合，并校验结构化结果。服务端显式挑选资料字段，避免直接透传整个数据库对象。

缓存键包含用户、scope、资料 ID、资料版本与内容指纹、工具名称和版本。即使业务版本号未变，金额变化也会改变摘要，避免命中旧键。读取前后版本不同则不写旧缓存键，但仍返回本次读取结果。

### Docker

固定检查程序通过 stdin 接收资料和规则 JSON，按金额、附件、规则计算结果。固定镜像及无外部网络、非 root、只读根目录和资源限制构成执行边界。

杀掉 Docker CLI 不等于停止容器。清理先核对所有权，再删除容器并回收 CLI；进程强制终止仍可能留下遗留容器，需要启动时检查。

### 安全与可观测性

认证决定身份，授权决定能否调用工具，脱敏限制数据内容。合法凭据和 API 202 不代表拥有工具权限。工具授权先于缓存读取。

脱敏器按字段名、文本模式和已配置秘密做确定性替换，不保证识别任意秘密。输入过滤后仍需 Schema 校验。

普通遥测 Hook 异常被隔离；事务内审计失败会使相应事务回滚。日志 on_tool 位于工具返回后、checkpoint 前，不能作为步骤已提交的证据。

API 与 Worker 不共享内存，持久统计从 PostgreSQL 聚合。监控按有限的状态和工具分类，不使用 task_id 作为指标标签；具体任务通过日志和 diagnostics 定位。

## 八 故障测试的阅读方法

读测试时依次确认：故障注入位置、确认故障发生的证据、故障后的持久不变量。

| 测试 | 如何制造条件 | 关键断言 |
|---|---|---|
| 认领回滚 | Attempt INSERT 触发数据库异常 | Task 回到认领前状态，没有新 Attempt |
| 外部效果窗口 | 效果提交后暂停响应，再杀探针进程 | 外部效果已存在，本地 Step 尚未提交 |
| 幂等恢复 | 新进程用原调用 ID 重发 | 发布请求 2 次，效果 1 次 |
| 不安全恢复 | 接管已派发的不安全调用 | UNKNOWN、NEEDS_REVIEW，没有第二次发布 |
| 旧 Worker 恢复 | SIGSTOP 停止心跳，租约自然到期，新进程接管后 SIGCONT | 旧进程资格失败，任务版本和步骤数未被推进 |
| 锁等待后资格检查 | 阻塞 guard，等待期间令租约失效 | 获得锁后抛 LeaseLost |

固定等待几秒不能证明故障窗口准确。持久屏障用已提交条件定位时机。最终 SUCCEEDED 不能单独证明无重复效果，还需检查独立业务服务的效果计数。

外部发布故障场景使用直接准备报告的独立探针，验证局部发布恢复，不代表完整 document_review 已通过。

## 九 问答中纠正的关键误区

| 最初的判断 | 本次澄清后的判断 |
|---|---|
| 没有新 Worker，旧身份匹配就能提交 | 租约到期本身足以拒绝提交 |
| 用户明确取消就应直接 CANCELLED | 有未知外部效果时优先 NEEDS_REVIEW |
| 新幂等键会返回新任务结果 | 仍需准入检查；原 Session 活跃时 SESSION_BUSY |
| 无发布任务在报告提交后崩溃，可能因发布未知转人工 | publish_mode=none 没有该发布窗口，恢复可继续 finish |
| Attempt 插入失败后 Task 保留 RUNNING，Attempt 为 FAILED | 同事务回滚，Task 为 QUEUED，没有新增 Attempt |
| 需要保留失败事务修改来证明故障 | 用异常、屏障或日志证明故障，业务状态仍必须保持事务原子性 |

学习者最后已能正确区分以下两个场景：

| 场景 | 数据库结果 |
|---|---|
| Task UPDATE 后 Attempt INSERT 失败，事务未提交 | 回滚为认领前状态；首次认领时为 QUEUED、fence=0、无新 Attempt |
| 认领事务已提交，Worker 随即崩溃 | Task 与 Attempt 暂时保持 RUNNING；不会自动回滚 |

第二种场景在任务未取消、未超时且可接管时，由后续认领把旧 Attempt 标为 LEASE_LOST，创建新 Attempt、递增 fence，并从持久 checkpoint 恢复。若尚未执行工具，checkpoint=-1，从第 0 步开始。

## 十 后续学习与接续位置

当前接续点：已完成“未提交事务失败”与“已提交后进程崩溃”的对比，学习者已正确回答两者区别。

下一阶段建议：

1. 不看提示，独立复述一次正常任务的执行过程，并标出各事务提交点。
2. 混合租约、取消、deadline 与外部效果条件进行场景判断，先列事实再推状态。
3. 选择现有测试实际运行，对照 Task、Attempt、Action、Ledger、Step 和业务效果记录解释结果。
4. 对照已有验收报告区分实现、已验证路径和未验证边界。

本次讲解覆盖完成，不等于工程运行验收完成。仓库原有验收记录记载 Docker 权限阻塞、完整审核链路尚未跑通；当前运行状态需要重新验证，不能由源码讲解推断。

## 十一 源码与阅读入口

- [推荐阅读顺序](source_reading_order.md)
- [Kernel 主循环](../core/kernel.py)
- [动作契约](../core/contracts/action.py)
- [确定性规划器](../core/nodes.py)
- [API 入口](../api/main.py)
- [数据库存储与事务](../runtime/storage/store.py)
- [Worker 编排](../runtime/orchestrator/worker.py)
- [工具运行时](../runtime/tools/invoker.py)
- [MCP 网关](../runtime/tools/mcp_gateway.py)
- [Docker 检查后端](../runtime/tools/docker_backend.py)
- [上下文引擎](../runtime/context/engine.py)
- [身份与权限](../runtime/security/policy.py)
- [脱敏实现](../runtime/security/redaction.py)
- [Hooks](../core/hooks.py) 与 [遥测指标](../runtime/observability/telemetry.py)
- [外部模拟业务服务](../examples/business_service.py)
- [存储集成测试](../tests/integration/test_storage.py)
- [进程恢复测试](../tests/process/test_recovery.py) 与 [场景实现](../examples/process_scenarios.py)
- [已有验收记录](../reports/20261022-reference/acceptance.md)
