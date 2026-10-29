> 本文件是上一阶段 20261024T135237Z-enhancement-p0 的历史核查快照，保留当时未初始化Git等事实。本轮已按 CODEX_MASTER_PLAN.md 建立版本并修改实现；当前结论与源码见 TASK_STATUS.json 和 delivery/20261024T142529Z-implementation，不能把本文件当最新状态。

# 增强前置核查基线（2026-10-24）

本轮只做核查、已授权的既有测试复现及实施拆分。**P0 未通过**：Docker 权限阻塞 A/C/F 完整链路；本轮仅复现 17 项单元测试，全部通过。没有实现 code_repair、RepoMap、真实模型或修复循环。

核查运行：`20261024T135237Z-enhancement-p0`。详细命令与证据见 [p0_revalidation.md](../reports/20261024T135237Z-enhancement-p0/p0_revalidation.md)，后续任务见 [enhancement_plan.md](enhancement_plan.md)。

## 范围与来源

| 项目 | 实际结果 |
|---|---|
| 当前路径 | `/home/dts/agent-runtime-core-reference`，独立参考工程 |
| Git commit / diff | **MISSING**；`git rev-parse HEAD` 和 `git status --short` 均退出 128：不是 Git 仓库。没有初始化仓库或伪造 revision |
| AGENTS.md | 根路径至当前目录的祖先未发现；参考工程项目目录检索未发现适用文件 |
| 附随增强方案 | **MISSING**：当前工程和可见附件位置未找到 `Agent_Runtime_Core_项目增强方案_v1.md`；已询问准确路径。本文和计划依据用户本轮任务书，不宣称已完成缺失方案逐项核对 |
| 本轮前文件指纹 | [baseline-file-hashes.json](../reports/20261024T135237Z-enhancement-p0/baseline-file-hashes.json)，SHA-256 `7926c6a3db022e92a5a9ae8641050ba969ec89af00f1c9ab0c0d9d991a338746`；它是文件清单，不是 Git commit |
| 正式学习工程 | 未读取、复制或修改 `agent-runtime-core`、month01–month04 源码；未执行其测试 |
| 运行边界 | 未使用 sudo、未改用户组/socket/系统权限、未执行 Compose/prune/卷删除、未启动服务或容器、未读取生产密钥、未推送或创建 PR |

已阅读实际 `architecture.md`、`design_coverage.md`、`failure_walkthrough.md`、`learning_walkthrough.md`、`source_reading_order.md`。项目唯一已有完整验收目录是 `reports/20261022-reference/`，检查了其 `acceptance.md`、`test-results.xml/json`、`demo-results.json` 和关联原始日志。目录名不是执行时间或 commit 的证据。

历史 `file-manifest.json` 与本轮前文件相比，四个文件 hash 不同：`core/contracts/action.py`、`core/kernel.py`、`runtime/storage/store.py`、`runtime/tools/invoker.py`。逐项旧/新 hash 见 [historical-manifest-diff.json](../reports/20261024T135237Z-enhancement-p0/historical-manifest-diff.json)。没有历史文件正文或 Git 对象，不能仅凭 hash 断言差异都是注释，也不能把旧测试直接认作当前源码全量回归。上述差异在本轮开始时已存在，本轮没有覆盖它们。

## 源码核对与冻结语义

| 核查点 | 实际源码与结论 |
|---|---|
| Ledger | `Store.prepare/dispatch/checkpoint/unknown` 使用 PREPARED / DISPATCHED / COMMITTED / UNKNOWN。迁移 `0001_reference.py:63` 还允许人工对账状态 CONFIRMED_APPLIED / CONFIRMED_NOT_APPLIED，`Store.reconcile` 实际写入。不能删掉这两个已有状态，也不能把早期设计枚举写回 |
| checkpoint | 表默认 -1；`Kernel.run`、`Store.prepare/checkpoint` 均使用最后完成步号 + 1；0 代表 Step 0 已完成。Step、Ledger、Session、Artifact 同事务提交 |
| 任务与 Planner | `TaskRequest.task_type` 仅允许 document_review；`NodeRegistry` 只注册 `DeterministicPlanner`；`ExecutionNode.plan` 当前是同步协议。无真实模型规划，无 code_repair |
| 动作身份 | `DeterministicPlanner.plan` 新动作生成 UUID；`Store.prepare` 固化 Action 与 Ledger；`pending_action` 优先恢复。Attempt 变化不重造同一逻辑 tool_call_id |
| Session | `Store.claim` 从 Task.context 重建并取 Session.version；`ContextEngine.build` 验证版本。Session.snapshot 是已提交上下文副本，不是新 Task 自动继承的对话记忆 |
| API | `api/main.py`：POST `/tasks`、`/tasks/{task_id}/cancel`、`/tasks/{task_id}/reconcile`；GET `/tasks/{task_id}`、`/diagnostics`、`/events`、`/artifacts/{artifact_id}`（后三者均在 task 路径下）；另有 `/health`、`/metrics`。没有 `/api/v1` 前缀 |
| 启动 | `examples/services.py` 启动 `uvicorn api.main:app` 绑定 127.0.0.1:18081，以及业务服务 18082；`make start` 不启动 Worker。Worker 入口 `python -m runtime.orchestrator.worker`；数据库本地端口 55439 |
| Artifact | `Store.checkpoint` 写 PostgreSQL BYTEA；单件 2 MiB、每 Task 8 MiB、平台 512 MiB。`diagnostics` 只给元数据；下载接口返回实际字节。现有表没有内容 hash 字段 |
| 恢复与权限 | 保留 PostgreSQL 事实源、租约/fence/CAS、授权先于缓存、prepare/dispatch 屏障、UNKNOWN 禁止 unsafe 重派发、NEEDS_REVIEW 占用 Session、取消/deadline 不被恢复重置 |
| 结果边界 | Runtime SUCCEEDED 与业务 PASS/NEEDS_DOCUMENTS 分开；未来 gate verdict 也必须单独表达。Trace/报告文件不是恢复依据 |
| 生产验收 | 未达到完整负载、重复故障或生产可靠性验收。旧记录明确未执行 270 次及全部 10/30 次重复实验；本轮也没有执行 |

六层文档在以上主要语义上基本与源码一致，但“已实测”是历史特定环境结论，不能外推到当前全部源码。

## 历史证据交叉核对

旧 XML 实际含 50 个 testcase：48 通过、0 failure/error、2 skipped。JSON 将这两项编码为 BLOCKED；二者统计一致。Docker 目录的三个通过项目是静态命令、开关拒绝及宿主固定脚本单元测试，都不能证明容器运行。本轮没有执行宿主检查脚本。

`acceptance.md` 的 A/C 等任务 ID 与当前 `demo-results.json` 不同。例如：

| 场景 | acceptance.md | demo-results.json |
|---|---|---|
| A DOC-001 | a184eb90-23d6-4e21-839c-16ce90912191 | b80034b3-af51-4d58-83a9-7c57d06ebbf9 |
| A DOC-002 | ea3128ff-03d1-4e5e-9f5a-6df5ddb3032f | fe8b82cd-9bae-4a01-b7be-cb030b9a96db |
| C | 26615510-c5c3-4f7d-9aa5-4b8d97844e49 | 84ba5834-add8-4616-9482-caf12c499ca7 |

应保留原件，逐批关联命令时间/日志，不能混用这些 ID。最新 JSON 中 A 两项均 FAILED/DOCKER_UNAVAILABLE、checkpoint=0、Artifact 为空；fetch 的 dispatch_count=0，关联 Worker 日志 MCP 读取标记也为 0，与缓存分支一致，**不能声称该轮 A 进行了真实 MCP 读取**。

历史 C 的首次 Worker 日志有 1 个真实 `ARC_REF_DOCUMENT_READ DOC-001` 标记，替代 Worker 日志为 0；替代日志本身是空文件，因此 0 仅表示没有读取标记，执行结束和第二 Attempt 还需结合该批 JSON 等证据。C JSON 最终 FAILED、full_report=blocked，不能称最终报告恢复成功。独立复核结果和日志 hash 已保存在 [historical-evidence-check.json](../reports/20261024T135237Z-enhancement-p0/historical-evidence-check.json)，明确标为历史证据。

## 已定位问题与最小修改建议（本轮未修）

| 编号 | 源码定位与事实 | 影响 / 最小建议 |
|---|---|---|
| B01 | `examples/demo.py:normal` 只核对 Artifact 数量和结果；A 可命中 fetch 缓存 | 完整证据缺下载字节/hash及独立 MCP 计数。增加专用 P0 证据导出，记录缓存策略，冷读取只处理演示身份/文档/版本的精确缓存项；不伪造调用次数 |
| B02 | `examples/process_scenarios.py:crash_checkpoint` 有真实屏障和 MCP 1→0 断言；`tests/process/test_recovery.py` 允许 status=blocked | 局部恢复 PASS 不代表最终报告。单独增加完整恢复 gate，要求终态、报告、BYTEA 内容/hash；保留原局部测试语义 |
| B03 | `examples/demo.py:security` 只调用 availability；Docker 可达即令 F status=passed，超时字段只是 `see docker test report` | F 汇总不能单凭可达性验收超时。连接真实 timeout 测试证据或独立执行启动屏障探针，未执行时必须独立标 MISSING/BLOCKED |
| B04 | `DockerBackend._inspect_owned` 对所有 inspect 非零返回 None；`test_real_timeout...` 通过 None 判断容器消失 | 权限/daemon/CLI 错误可能被误认为已删除；代码级风险，未容器复现。仅明确 No such container 可视为缺失，其他错误 fail-closed；保留 inspect 原始退出码、stderr、die/destroy 等独立证据 |
| B05 | `cleanup_orphans` 按 project 标签列举；Compose postgres 也有相同标签，名称却非 arc-ref-tool-；`_inspect_owned` 会抛 ownership mismatch | Compose postgres 存在时可能让 Worker 启动清理中断；源码推导，当前未运行 Compose。列举时缩小工具资源范围或跳过非工具，仍保留删除前二次归属校验 |
| B06 | `worker.main_async.attempt_active(attempt_id, fence)` 忽略 fence，仅查 `Store.active_attempts()` | DB 写回仍受 fence 保护，但容器清理没有核对完整标签身份。最小增加按 Attempt + fence + 有效 lease 的只读判定；分别测试有效容器保留、失效删除、未知归属拒绝 |
| B07 | `runtime/tools/invoker.py` 新增阅读注释称 MCP 网关“签名和权限检查”，publish 注释泛称服务必须幂等且可查询 | `MCPDocumentGateway` 无签名实现，授权位于 ToolRuntime；unsafe 本来就是支持的模式。后续只修误导注释，不据此增加或虚构能力 |
| B08 | Kernel 的 observation key、report Artifact、finish(context['report']) 写死；Middleware 默认步预算 4；ContextEngine 依赖审核规则；ToolRegistry/Policy 仅现有工具 | 新任务不是加一个 Planner 类即可工作。必须小步拆出类型化任务绑定、上下文/结果投影、明确步预算与工具授权，逐步回归原审核任务；不能全层重写 |

## 已证实与缺证

- 本轮证实：当前源码上述静态事实；Python 3.11.17 等本地版本；Docker client/context/socket 权限；17 个既有单元用例通过。
- **BLOCKED**：A 两份标准最终报告及 BYTEA；C 新 Worker 恢复到最终报告；F 容器真实启动后超时终止、归属清理；实际 daemon 版本和已安装镜像 digest。
- **MISSING**：方案附件、Git commit/diff、当前 A/C/F task/attempt/tool_call 身份、实际 MCP 次数、容器启动/退出/清理记录、最终 Artifact 内容/hash。没有提交这些任务，所以不存在可填写的新 ID。
- 真实模型：本轮不读取任何密钥，也没有已配置可调用后端的证据；后续真实接入保持 BLOCKED，禁止静默 Fake 回退。固定 digest 配置不等于本机镜像存在。

变更审计以本轮前后 hash 为准，见 [workspace-changes.json](../reports/20261024T135237Z-enhancement-p0/workspace-changes.json)。仅新增本轮文档与证据，未改 Kernel、Store、测试断言、依赖锁或正式学习工程。
