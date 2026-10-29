> 2026-10-26 当前更新：Docker已恢复可访问，P0、独立Harness、容器矩阵和修复恢复均已有真实证据。下文早期验证边界作为历史说明保留；当前权威状态见根目录TASK_STATUS.json及新delivery报告。真实模型仍未授权，不能以Fake通过代替。

> 2026-10-24 更新：下文保留原 document_review 教学基线；新增实现与当前验证边界见 [修复阅读路径](repair_reading_path.md)、[验证器契约](verifier_contract.md)、[Badcase](badcases.md) 和根目录 TASK_STATUS.json。旧验收数字不代表本轮结果；Docker/真实模型仍 BLOCKED。

# 能力覆盖与证据边界

“已实现并实测”只说明对应断言在记录的环境运行过，不等于生产可靠性证明。下面按独立能力区分，不把 Protocol、空类或配置项的存在当成已验证实现。最终 passed / failed / skipped / blocked 数量与 Task ID 以 `reports/<run_id>/acceptance.md`、`test-results.*` 和 `demo-results.json` 为准。

本轮真实环境基线包括 Python 3.11.17、PostgreSQL 17.11、官方 MCP Python SDK 2.2.0 与 2026-07-28 协议。2026-10-26在有效Docker组会话中已核验固定镜像digest并运行真实容器，见当前environment.json。

| 层与能力 | 分类 | 实现与验证依据 |
|---|---|---|
| L1 契约、节点白名单、结构化 Plan | 已实现并实测 | `core/contracts`、`NodeRegistry`、`DeterministicPlanner`；严格 budget 与 AST 导入方向测试 |
| L1 显式 Preparation / Checkpoint Barrier | 已实现并实测 | `Kernel.run`；`tests/unit/test_kernel.py` 验证调用顺序，PG 测试验证事务提交关系 |
| L1 普通遥测 Hook 失败隔离 | 已实现并实测 | `Hooks.emit`；损坏 Hook 不改变已提交业务结果的单元断言 |
| L1 真实模型推理、开放式 Agent 规划 | 参考版主动缩减 | 使用确定性规划器，只选择动作；不宣称 LLM 推理能力 |
| L2 静态工具 Schema、参数摘要、效果类别 | 已实现并实测 | `ToolRegistry.validate`、真实 ToolRuntime 边界测试 |
| L2 MCP stdio 协议发现和调用 | 已实现并实测 | `MCPDocumentGateway`、`tests/mcp`；真实独立 stdio 进程与数据库文档读取，2026-07-28 路径不手写 v1 握手 |
| L2 权限先于只读缓存、版本化缓存键 | 已实现并实测 | `ToolRuntime.invoke / _cache_key`；受限身份不能通过缓存绕过授权 |
| L2 本地效果与 Ledger / checkpoint 原子提交 | 已实现并实测 | `Store.checkpoint` 的 local_effects 分支与 PG 事务测试 |
| L2 外部幂等、查询、unsafe 恢复分支 | 已实现并实测局部发布故障 | `ToolRuntime._publish / _query / _unknown`、`tests/process/test_recovery.py`；独立服务、稳定 tool_call_id 和业务效果计数。局部 publish 故障演示不代替完整审核链路 |
| L2 固定镜像、非 root、只读根、无网络、资源参数 | 已实现并实测静态命令；真实隔离未实测 | `DockerBackend.command`；静态参数测试只能说明命令构造，不证明 Docker 应用了限制 |
| L2 Docker 真执行、墙钟超时、真实终止及遗留清理 | 已实现并实测 | `DockerBackend.check / cleanup_orphans`；P0及25次生命周期矩阵，候选只在容器执行 |
| L3 持久受理、幂等与 Session 占用 | 已实现并实测 | `Store.submit`、部分唯一索引、真实 PG 事务测试；幂等检查先于 SESSION_BUSY |
| L3 认领与 Attempt INSERT 原子性 | 已实现并实测 | `Store.claim`；真实 INSERT 失败触发器验证认领回滚 |
| L3 Lease / fencing / CAS、旧 Attempt 写回拒绝 | 已实现并实测 | `_locked`、租约过期后认领与旧写回测试；锁后读取数据库时间 |
| L3 取消、deadline、NEEDS_REVIEW、人工 reconcile | 已实现并实测事务边界 | 独立 operator 身份、版本 CAS、证据摘要、取消/到期/其他未知效果阻塞检查 |
| L3 独立 Worker 退出与恢复 | 已实现并实测，完整审核报告通过 | `worker.execute`、真实P0 C、repair-recovery；三个恢复点和活容器SIGTERM均有证据 |
| L3 SSE 最新快照与重连语义 | 已实现并实测 API 基础与断流语义 | `api.main.events / validate_event_id`、`tests/integration/test_api.py` 与演示 E；不是逐条历史事件重放 |
| L3 Artifact BYTEA 三层原子配额 | 已实现并实测 | `Store.checkpoint`、artifact_quota 行锁；单件 2 MiB / 单 Task 8 MiB / 平台 512 MiB |
| L4 规则检索、引用身份/版本/source 检查 | 已实现并实测 | `Store.rules`、`validate_citations` 与单元测试 |
| L4 有界工作记忆、确定性摘要、Protected Facts | 已实现并实测 | 长历史压缩、故意丢事实拒绝、超预算拒绝；估算计数显式标记 |
| L4 Session 版本与 checkpoint 重建 | 已实现并实测 | `ContextEngine.build`、`Store.checkpoint`；不一致版本拒绝 |
| L4 同 Session 的跨 Task 对话历史继承 | 参考版主动缩减 | Session 摘要和快照用于同一 Task 跨 Attempt 恢复；新的 Task 不自动带入旧 Task 的业务文档或工作记忆 |
| L5 演示凭据、静态授权、配额/速率 | 已实现并实测 | `policy.py` 定义规则，`Store.submit` 事务内原子检查与预留 |
| L5 调用前 / 审计 / 返回后三道保护 | 已实现并实测明确样例 | `redaction.py`；嵌套敏感键和模拟秘密不出现在测试 Trace/输出中；不是任意秘密识别保证 |
| L5 审计不可用时副作用 fail-closed | 已实现并实测 | `Store.dispatch` 事务内审计；真实审计 INSERT 失败使派发事务回滚 |
| L5 SSO / 产品级 RBAC / 强多租户沙箱 | 参考版主动缩减 | 只有本地演示 reviewer / restricted / operator 三个身份 |
| L6 关联 Trace 与低基数指标 | 已实现并实测 | Task/Attempt/Step/Tool Call 放 Trace；Prometheus 只用固定分类；API 从 PG 只读聚合获得跨进程状态 |
| L6 追加 Audit、只读 diagnostics | 已实现并实测 | DB 触发器拒绝 UPDATE/DELETE；diagnostics 用 REPEATABLE READ READ ONLY 一致快照 |
| L6 数据库超级用户防篡改证明 | 未实现 | 普通应用追加保护不能对抗超级用户；没有虚构不可篡改承诺 |
| 270次确定性负载、10/30次重复故障 | 已有真实执行入口与本轮原始数据 | 90+50故障通过；270提交结果及拒绝分母见本轮负载报告，不构成生产或个人W8验收 |
| Redis / Outbox / K8s / 多 Agent / 第二套框架 / 新前端 | 参考版主动缩减 | 不属于此最小闭环的实现范围 |

## 如何判断一项能力确实有证据

1. 先看对应场景的最终状态，而非仅看“测试文件存在”。
2. 区分单元断言、真实数据库断言、独立进程故障和真实 Docker/MCP 交互。
3. 检查失败前已提交的事实，以及故障后由哪个组件独立确认结果。
4. 对外部效果同时看 requests 与 effects；不要用 Runtime 自报成功替代业务服务的效果数据。
5. 对 BLOCKED 保留原因与未覆盖语义；全部集成测试跳过绝不能写成完整跑通。

报告导出文件不是恢复事实源；数据库不可用时，拒绝审计可能无法持久化。受控测试没有证明任意网络故障、磁盘损坏、数据库故障转移或恶意扩展都能安全处理，也没有吞吐量、延迟或生产规模承诺。
