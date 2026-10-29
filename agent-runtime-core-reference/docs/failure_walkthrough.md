> 2026-10-24 更新：下文保留原 document_review 教学基线；新增实现与当前验证边界见 [修复阅读路径](repair_reading_path.md)、[验证器契约](verifier_contract.md)、[Badcase](badcases.md) 和根目录 TASK_STATUS.json。旧验收数字不代表本轮结果；Docker/真实模型仍 BLOCKED。

# 从已提交事实解释故障

统一执行 `make demo`，依次运行 A–G 并把每个场景的 Task ID、检查结果及阻塞原因写入 `reports/<run_id>/demo-results.json`。回归使用 `make check`。这些命令由测试代码设置明确屏障；不要用“睡两秒后杀进程”推测副作用窗口。

测试开关来自受信 Worker 的 `ARC_TEST_MODE / ARC_TEST_BARRIER` 配置以及测试数据库控制记录。普通 TaskRequest 不接受故障脚本、任意镜像、暂停响应或进程控制字段。本轮 Docker 权限不足，所以 A 的完整报告、C 恢复后的最终报告、F 的真实容器超时终止是 BLOCKED；不能把局部验证包装成完整跑通。

## A. 正常审核，也是后续故障的基准

入口：`make demo` 中 A。提交 DOC-001 与 DOC-002，读取数据库规则，经真实 MCP 获取文档，再走固定 Docker 检查、报告与 Artifact。

教学期望是 DOC-001 金额 12000、无审批附件，结果 NEEDS_DOCUMENTS；DOC-002 金额 8000、有发票，结果 PASS。业务规则不判断 document_id。Docker 不可用时，测试应保留已受理、已检索和已获取文档的事实，并明确最终结果未验证；不能硬编码返回这两个期望值。

源码：`DeterministicPlanner.plan`、`MCPDocumentGateway.fetch`、`DockerBackend.check`、`ContextEngine.observe`、`Store.checkpoint`。观察任务详情和 diagnostics 中的引用、Step、Ledger、Artifact；只有报告步骤实际提交后才应有对应 BYTEA。

## B. 请求重放与键冲突

入口：`make demo` 中 B。对同一 principal，使用同一个幂等键发送相同请求两次，再改变请求复用该键。

第一次接受事务已写入 Task 与幂等键。第二次只能返回原 Task，不能因 Session 已活动而误报 SESSION_BUSY。第三次返回 409，逻辑 Task 数仍为 1。顺序由每次 HTTP 响应后的数据库查询确定，不依赖并发时序猜测。

源码：`Store.submit`。这证明的是接收边界幂等，不能据此推出外部业务效果已经具备幂等能力。

## C. checkpoint 已提交，Worker 退出

入口：`make demo` 中 C。独立 Worker 的 `CheckpointTestHook` 在 `after_checkpoint:0` 屏障等待；测试读到 `arc_ref.test_barriers.reached=true` 后才终止 Worker。

此时 Step 0、原 tool_call_id、Ledger COMMITTED、Task checkpoint=0 与 Session 快照已经提交，下一步还没开始。新 Worker 取得新 Attempt / fence 后从 step_no=1 开始，不能重新执行已完成的 MCP 获取动作。

要分别检查三件事：task_id 不变、attempt_id 改变、已完成步骤实际调用次数不增加。MCP stderr 的 `ARC_REF_DOCUMENT_READ` 是真实读取证据；Ledger 自报的 dispatch_count 不足以单独证明没有发生第二次底层调用。

本轮即使上述恢复边界得到验证，后续 Docker 检查和最终报告仍然被环境阻塞。源码：`Store.claim / pending_action / checkpoint`、`Kernel.run`、`worker.execute`。

## D. 外部效果已发生，Runtime 尚未确认

入口：`make demo` 中 D。测试使用独立模拟业务服务与独立 Worker。业务服务先提交 `arc_business.requests / effects`，退出自己的事务，再设置 `business:<tool_call_id>` 屏障并暂停响应。测试确认屏障后终止 Worker，再启动新 Worker。

本轮这个场景可直接准备独立的 report.publish Action，验证外部发布恢复语义；它是局部故障测试，不等同于 Docker 审核与 Artifact 已完整跑通。测试用报告应明确标为故障夹具，不能当作真实标准流程生成的业务报告。

### 支持幂等键

故障前 Runtime 已提交 DISPATCHED，业务服务已提交一次效果；Runtime 还没有 COMMITTED。新 Attempt 读取原 Action，用原 tool_call_id 再发送。服务可以收到两次请求，但 `arc_business.effects` 对幂等键只有一个效果。这里保证的是依赖服务去重后的单效果，不是“网络只请求一次”。

### 无幂等、无查询

故障前事实相同，但新 Worker 不能安全判断是否已执行。它把 Ledger 置 UNKNOWN、Task 置 NEEDS_REVIEW，不再发送第二次请求。材料缺失不触发这种状态。

### 可查询模式

已有 DISPATCHED 的 queryable 动作先调用 `/effects/{tool_call_id}`。肯定 applied 的响应可以作为确认；404 或超时不能证明“没有发生，也不会在以后发生”，因此仍进入 UNKNOWN / NEEDS_REVIEW。

源码：`ToolRuntime.invoke / _publish / _query / _unknown`、`examples/business_service.py:publish / query_effect`。比较实际请求和效果时分别查询，避免 JOIN 把次数乘大：

```sql
SELECT count(*) AS request_count
FROM arc_business.requests WHERE task_id = '<task_id>';

SELECT count(*) AS effect_count
FROM arc_business.effects WHERE task_id = '<task_id>';

SELECT tool_call_id, state, dispatch_count
FROM arc_ref.ledger WHERE task_id = '<task_id>'::uuid;
```

以上 SQL 是诊断模板。实际自动演示会保存对应计数，不依赖读者手工操作外部服务。

### 运维 reconcile

普通审核身份不能对账；使用独立 operator 身份调用 `POST /tasks/{task_id}/reconcile`，提供当前 Task version、tool_call_id、证据说明以及以下一个结果：

- CONFIRMED_APPLIED：以证据确认已生效，不再派发该效果。
- CONFIRMED_NOT_APPLIED：以证据确认未生效，只有所有阻塞条件解除后才允许继续。
- INCONCLUSIVE：没有充分证据，保留 NEEDS_REVIEW。

Store 保存证据 SHA-256 摘要及运维审计，使用版本 CAS 避免两次对账互相覆盖。它重新检查 deadline、取消意图和所有未确认效果；已经过期或被取消的任务不会因为人工对账而重新获得预算。NEEDS_REVIEW 继续占用 Session。

## E. 竞争认领、旧租约与取消顺序

入口：`make demo` 中 E，真实事务回归位于 `tests/integration/test_storage.py`。

两个 Worker 同时认领同一个 Task，行锁确保只有一人成功。租约失效后，新 Attempt 获得更大的 fence；旧 Attempt 即使恢复运行，`Store._locked` 也会因 attempt/fence/lease 不符拒绝写回。DB 获取行锁之后再读时间，避免用等待锁之前的陈旧时间判断有效租约。

心跳失败时 Worker 进入 LEASE_UNCERTAIN，取消活执行并尽力停止受控容器。租约不能撤销已经发送的 HTTP 请求，所以未确认外部效果仍需保守处理。

取消与成功的先后通过数据库提交顺序明确验证：取消先提交，后续 finish 被拒绝；成功先提交，后来的取消不能改写 SUCCEEDED。SSE 断开仅结束观察，持久 Task 的 `cancel_requested` 不变。

取消是阻止后续动作的意图，不是跨系统回滚。已经写入的 Step、Artifact 或外部业务效果都不会因为浏览器断线或取消按钮被撤销。

## F. 拒绝路径、审计不可用与资源约束

入口：`make demo` 中 F；工具边界测试见 `tests/mcp/test_tool_runtime.py`；真实 Docker 测试见 `tests/docker/test_backend.py`。

受限 principal 即使面对已缓存结果，也先被拒绝，实际 MCP/工具执行次数应为 0。故意让 Audit INSERT 失败时，dispatch 事务整体回滚，独立业务服务的 request_count 应为 0；不能先调用后补审计。

三道过滤分别在派发前、写审计时和处理返回后执行。测试注入 `SIMULATED_SECRET_DO_NOT_LEAK` 与敏感键，检查响应、Trace 和审计都没有原值。这只是明确演示的敏感字段保护，不是对任意隐写或未知秘密的完整 DLP 承诺。数据库完全不可用时可能无法持久化拒绝审计，必须保留这个观测缺口。

Docker 超时测试先等待容器脚本输出 `ARC_REF_STARTED`，确认进程真的运行，再等墙钟限制触发并检查容器已消失。仅检查 CLI 退出码或 kill Docker CLI，都不能证明容器内进程已经停止。本轮该真实测试被 Docker 权限阻塞，静态命令参数检查不能替代它。

清理只匹配 `arc-ref-tool-` 名称和项目标签；启动恢复先查 DB 判断 Attempt 是否仍有效，不能误杀另一个仍在工作的 Worker 容器。禁止全局 prune 和删除未知资源。

## G. 长历史、坏摘要与上下文恢复

入口：`make demo` 中 G；确定性回归在 `tests/unit/test_context.py`。

构造长工作记忆，触发 `ContextEngine._compact`。检查规则引用、金额、附件及文档版本仍在 Protected Facts 中。故意删除金额字段的候选摘要必须被 `apply_summary` 拒绝；不能因摘要更短就接受关键事实丢失。

重建时用保存的 Session 版本和 checkpoint 内容创建新 Execution，然后 `ContextEngine.build` 校验一致性。上下文持久化依赖 PostgreSQL 快照；reports 导出、Trace 日志和临时目录都不参与恢复。

## 阅读结果时保留边界

本轮每个确定性场景至少尝试运行一次；BLOCKED 与 PASSED 必须分开。尚未完成完整 270 次压测，也未完成全部 10/30 次重复实验；没有正式 W8 验收结论。再次运行前读最新 acceptance，结束后 `make stop`，需要停止数据库则 `make db-stop`，保留参考数据以便复查。
