> 历史阶段方案，已被 CODEX_MASTER_PLAN.md 的实施授权与结果契约取代。旧附件缺失不再阻塞。本轮文件映射与实现差异见 [implementation_mapping.md](implementation_mapping.md)，当前状态见 TASK_STATUS.json；下文原始计划保留用于追溯。

# Agent Runtime Core Reference 增强实施拆分

状态：**设计交付，尚未实施**。依据本轮任务书和当前源码；缺失 `Agent_Runtime_Core_项目增强方案_v1.md`，收到后需要做差异复核，不能称本计划已与附件完全一致。当前工程没有 Git commit，按 [基线](enhancement_baseline.md) 的文件清单定位。P0 A/C/F 仍 BLOCKED；[本轮复验](../reports/20261024T135237Z-enhancement-p0/p0_revalidation.md) 只有 17 项单元测试通过。

目标是保留六层，加入受控 Python code_repair 教学案例，不建设完整 Coding Agent。下文“新增/修改”均为**未来实施范围**，本轮没有创建任何空实现、模型客户端、迁移或测试夹具。每包交付可独立评审；依赖关系不代表已授权修改核心。

## 冻结项与必须正视的接入点

1. 保留 Ledger PREPARED/DISPATCHED/COMMITTED/UNKNOWN，以及已有人工对账 CONFIRMED_APPLIED/CONFIRMED_NOT_APPLIED。不添加早期设计中的另一套状态替换它们。候选状态与 gate verdict 用独立字段。
2. checkpoint 是最后完成步号，初值 -1，下一步 +1；candidate_no 与 step_no/attempt_id 是三个概念。保持 prepare 和 checkpoint 的事务屏障及原有 Session 版本规则。
3. Task 不随 HTTP/SSE 生命周期消失；同一 Task 恢复改变 Attempt/fence，不自动增加 candidate_no。已持久动作恢复使用原 tool_call_id；新候选的新操作用新 UUID，即使参数/patch 相同。
4. PostgreSQL 是唯一持久恢复依据。Action、patch 字节及 hash、候选、验证版本/结果必须可恢复；日志、reports、旧容器磁盘和缓存都不是事实源。
5. 保留授权先于缓存、可信服务选择工具/镜像/身份、取消/deadline/lease/fence/CAS、审计失败禁止派发副作用、UNKNOWN 禁止 unsafe 自动重发。没有跨 Task 对话记忆承诺。
6. 原 document_review API 路径/行为和现有测试继续保留。Artifact 仍 BYTEA，沿用配额；不把验证输出直接塞入现有每 Task/Step 唯一 Artifact 槽而覆盖报告。
7. 当前 `ExecutionNode.plan` 为同步方法；Kernel 写死四个工具的观察投影、report 字节和 finish；Middleware 默认 max_steps=4；TaskRequest 只接受审核字段；ContextEngine 必须读取审核规则；ToolRegistry 与 Policy 也仅接受现有工具。必须按下列接入小包解决，不能宣称只插入新 Planner 就可运行。

所有涉及契约、Kernel、Store 的未来修改都需在对应工作包中明确审查；本轮冻结未改变。采用现有 psycopg/Alembic、现有 Worker/ToolRuntime/ContextProvider 接口，不引入第二套 Agent 框架。

## 独立 Harness 的输入、信任边界和判定

CaseManifest 由受信平台固化，至少包含：`case_id`、`manifest_version/hash`、`base_revision`（case 仓库确切 commit）、源码 bundle hash、`image_digest`、`test_suite_hash`、`expected_test_ids`（含参数化完整 node ID）、允许修改的源码路径、受保护文件 hash、固定 runner/验证器版本、固定 argv、资源/时间/输出上限、预期基线失败测试、debug/holdout 分组。任何未知 revision/digest/hash 不得填占位值执行；本参考工程没有 Git 不妨碍设计，但具体 case 必须另有合法、固定来源，禁止从 month01–month04 复制。

Harness 独立于 Planner，接收已校验的 manifest 和候选内容，受信控制器决定命令和镜像。默认禁止改 `tests/**`、任何 `conftest.py`、依赖锁、pytest 配置（包括 pyproject/setup.cfg/tox.ini/pytest.ini 中相关设置）、平台代码和 Manifest。只允许清单明确列出的普通 Python 源文件；拒绝越界、绝对路径、符号链接、二进制补丁及模糊应用。候选视为不可信数据；基准与套件只读，补丁只进候选源码区域。

gate verdict 规划为 PASS/FAIL/INVALID/BLOCKED：PASS 要求固定测试集非空，收集/实际执行 ID 与清单完全一致、全部 passed、无 skip/xfail/xpass/collection error、进程退出正常、报告有效、输入和受保护文件完整性一致。测试失败为 FAIL；空测试、skip、配置篡改、缺失/重复 ID、损坏/伪造结构报告为 INVALID；环境不可用为 BLOCKED。BLOCKED/INVALID 均不可转 PASS。单独保留 pytest exit code、实际 counts、reason 和 schema/version，不仅解析一个“passed”字符串。

人工正确 patch 和错误 patch 由夹具维护者预备，只用于验证器自测；正确 patch 的真实通过必须由固定套件证明。黄金 patch 不给 Planner、RepoMap、模型上下文或留出评测操作者查看。验证器控制层在候选进程外，可信固定 runner 采集结果；只读测试文件不等于能对任意恶意 Python 证明测试结果真实性，首期界定为受控教学缺陷，不宣称对主动攻击测试解释器的任意仓库提供安全证明。

## 工作包与预计工作量

估算为单人净开发/验证时间，**半天按 4 小时**；不含管理员等待、镜像下载等待、模型后端开通和评审排期。任何包实作预估超过 4 小时，必须先按失败路径或组件再拆分，不能把超时工作隐藏在包内。依赖阻塞时停止相关验证，不能把“写了代码”算作通过。每行列出 2–3 个验收断言；跨包端到端验收另列，不能用一行表格掩盖整层重写。

### R：先补当前 P0 的可验证性

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言（均需真实证据或明确限定单元范围） | 风险与边界 |
|---|---|---|---|---|
| R00 环境与输入补齐，1–2h | 环境所有者提供已授权 daemon 会话；提供方案文件 | 仅更新 `docs/enhancement_baseline.md`、新 `reports/<run_id>/p0_revalidation.md` | endpoint 可达且实际镜像 digest 匹配；附件逐项差异登记；没有权限提升/正式工程访问 | 外部等待不计工时；当前 BLOCKED/MISSING |
| R01 A 证据导出，3–4h | R00；B01 | 新 `examples/p0_revalidate.py`，改 `examples/demo.py`，新 `tests/integration/test_p0_artifact_evidence.py` | DOC-001/002 均到最终报告且业务结果正确；下载字节等于 PG BYTEA 并记录 SHA-256；冷读取独立 MCP 标记与缓存策略/Task/Attempt/Call 映射完整 | 不允许靠缓存的 Ledger 数字声称真实读取；导出超配额明确失败 |
| R02 C 完整恢复 gate，3–4h | R01；B02 | 改 `examples/p0_revalidate.py`、`examples/process_scenarios.py`；新 `tests/process/test_p0_complete_recovery.py` | 提交后屏障退出、新 Attempt 且 checkpoint 从 0→1；原 Step/Call 不变且 MCP 1→0；恢复最终报告/Artifact 与 A 相同基准一致 | 保留原局部恢复测试；只杀本轮归属进程；导出原始前后快照 |
| R03 inspect 失败关闭，2–3h | B04；单元部分可先做，真实部分等 R00 | 改 `runtime/tools/docker_backend.py`，新 `tests/docker/test_inspect_errors.py` | 明确不存在才返回 None；permission/daemon/解析失败不能变“已删除”；清理仍先查名称/标签再删除 | 当前实现有误判风险；单元模拟错误不能算 F 通过 |
| R04 F 超时证据，3–4h | R00、R03；B03 | 改 `examples/p0_revalidate.py`、`examples/demo.py`、`tests/docker/test_backend.py` | ARC_REF_STARTED 后才计运行超时；保存容器 die/退出状态与 destroy/最终不存在证据；未跑探针时 F 不标 PASS | --rm 使事后 inspect 无法取 ExitCode，需预先订阅限本容器事件；直接 Backend 探针无 Ledger 时明确 N/A |
| R05 归属清理回归，3–4h | R00、R03；B05/B06 | 改 `runtime/tools/docker_backend.py`、`runtime/orchestrator/worker.py`、`runtime/storage/store.py`；新 `tests/docker/test_owned_cleanup.py` | 当前 Attempt+fence+lease 容器保留；失效且确属本项目工具容器删除；同项目 postgres/未知归属不删、不导致错误删除其他资源 | 仅新增精确只读有效性查询，不重构 Store；DB 不可用停止清理 |

R01/R02/R04/R05 通过后生成独立 P0 acceptance，禁止仅汇总 exit=0。B07 的误导注释可在另一个 ≤1h 文档/注释补丁修正，检查 MCP 无签名、unsafe 分支含义与实际代码一致；本轮不顺手修改。

### H：独立 Test Harness（先于模型）

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言 | 风险与边界 |
|---|---|---|---|---|
| H01 固定 CaseManifest，2–3h | 附件差异复核；case 来源明确 | 新 `core/contracts/repair.py`、`runtime/repair/manifest.py`、`tests/repair/test_manifest.py` | 缺 base_revision/image_digest/test_suite_hash/预期 ID 拒绝；清单规范化 hash 稳定且 ID 非空唯一；源码允许列表与保护文件冲突拒绝 | 这是下一项最小增强；有完整输入校验与测试，不是空 DTO；不挂入 API |
| H02 独立 Verdict，3–4h | H01 | 新 `runtime/repair/verdict.py`、`tests/repair/test_verdict.py`；扩 `core/contracts/repair.py` | 正常完整报告才 PASS，错误 patch 的失败结果为 FAIL；空/skip/xfail/漏 ID/重复 ID 为 INVALID；坏报告、退出码和 counts 冲突不得 PASS | 此时用固定记录验证判定规则，仅是验证器单元验收，不冒充真实 patch 通过 |
| H03 补丁约束与重建，3–4h | H01 | 新 `runtime/repair/workspace.py`、`tests/repair/test_workspace.py` | 只对固定基准精确应用允许源码 patch；tests/conftest/锁/配置/平台篡改与路径逃逸拒绝；基准+同 patch 重建文件树 hash 一致 | v1 只支持有限普通文本 Python diff，不做通用 Git 补丁系统 |
| H04 固定 case 与套件打包，3–4h | H01、H03 | 新 `examples/repair_cases/debug_case/`（原创最小 Python 包、固定测试、manifest）、`deploy/repair/Dockerfile`、`deploy/repair/requirements.lock`、`examples/repair_images.py` | 未修基线必失败于预定 ID；受保护套件 hash 可重算；构建后记录实际 digest 而非仅 tag | 不复制正式工程；镜像构建/容器验收等 R00；依赖只用于可信镜像构建 |
| H05 受限 Harness runner，3–4h | H03/H04、R03 | 新 `runtime/repair/runner.py`、`deploy/repair/runner.py`、`tests/docker/test_repair_runner.py` | 固定 argv/镜像、非 root/无网络/只读保护文件且无平台凭据；超时和输出超限停止并留证；候选不能传 shell/镜像/身份参数 | 不放宽现有 DockerBackend 的单镜像约束；新 backend 仅服务端配置批准镜像 |
| H06 真实 Harness 矩阵，3–4h | H02/H05、R00 | 新 `tests/repair/test_harness_acceptance.py`、`tests/repair/fixtures/`（受控 patch/损坏报告夹具）；改 `runtime/repair/runner.py` | 人工正确/错误 patch 分别真实 PASS/FAIL；空测试/skip 不得 PASS；配置或报告篡改 INVALID，原始输出保留 | 六类用例不因环境跳过就验收；报告由可信 runner 采集，限制受控教学威胁范围 |

H01/H02/H03 可在已授权实现后离线开发；H04–H06 的真实容器结论必须等 R00。先完成 H06，才能用模型修复成功率作结论。

### M：最小 RepoMap

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言 | 风险与边界 |
|---|---|---|---|---|
| M01 Python AST 索引，3–4h | H01/H03 | 新 `runtime/context/repo_map.py`、`tests/repair/test_repo_map.py` | 提取文件/类/函数/导入/直接语法引用及行区间；结果绑定 revision+tree hash；语法错误、动态属性/反射标 unresolved，不猜完整调用边 | 只读允许 Python 文件，不执行 import，不解析其他语言，不宣称动态调用图 |
| M02 相关片段选择，2–3h | M01、H02 | 新 `runtime/context/repair_context.py`、`tests/repair/test_repair_context.py` | 由缺陷描述、失败测试位置选直接相关片段；预算超限明确截断说明或拒绝，保留路径/版本/行号；黄金 patch 和未授权路径不进入上下文 | 测试输出与源码内容视为数据，不能覆盖系统授权；确定性筛选不冒充语义检索模型 |

### P：统一 Planner 与真实模型 Adapter

当前锁文件没有本计划选定的模型 SDK，也没有已核实的模型后端。不得使用“最新 SDK”或猜测模型名替代兼容性检查；本轮没有读取凭据或发起模型请求。

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言 | 风险与边界 |
|---|---|---|---|---|
| P01 统一模型规划 Adapter 契约，2–3h | H06 | 新 `core/contracts/planning.py`、`tests/repair/test_planner_protocol.py` | Fake/Real 共享结构化输入输出协议；沿用现有 ExecutionNode/PreparedAction 外层边界，已准备生成调用才允许调用 Adapter；取消/剩余预算能传入异步模型接口 | 当前仅有同步 ExecutionNode，不伪称已有网络 Planner；本包不改 Kernel 同步 plan 调用 |
| P02 后端兼容性核对，2–3h | P01；受信配置指定 provider/model | 新 `docs/model_backend_compatibility.md`，改 `config/settings.py`；如确需 SDK 才改 `requirements.lock`；新 `tests/repair/test_model_config.py` | 记录指定 SDK/模型/官方文档 URL/核对日期及结构化输出支持；缺配置/后端凭据显式 BLOCKED；选 Fake 是显式配置，不作失败回退 | 当前 BLOCKED；仅检查批准的凭据入口是否可用，不输出值；新版本不自动升级 |
| P03 结构化 Real Adapter，3–4h | P02、M02 | 新 `runtime/planning/model_adapter.py`、`runtime/planning/fake_adapter.py`、`tests/repair/test_model_adapter.py` | 同协议返回受 Schema 限制的候选意图/patch，不含任意 shell/镜像/网络授权；拒答/坏 JSON/超时均明确失败；真实 smoke 留 model/request ID/版本证据，缺后端仍 BLOCKED | 重试次数必须显式有界且不偷偷形成第四候选；调用取消和超时受 Task 剩余预算约束 |

P01 在现有 ExecutionNode/PreparedAction 边界内统一 Fake/Real 的模型规划接口，不让网络请求发生在 Kernel.prepare 之前：外层确定性节点先准备 `repair.generate` 动作，L2 派发后才调用共享异步 Planner Adapter 产出候选 patch。外层流程仍是确定性控制；真实模型贡献是候选生成，不宣称模型自主规划工具序列。结构化意图的工具名、effect_class、task/step/call ID 和身份由受信构造器赋值/验证，模型输出不能直接作为授权事实。服务端模型网络调用与工具容器无网络边界分开，容器不获得模型凭据。开发期 Fake 可先支持确定性可靠性测试，但不得计入真实模型评测。

### D：三候选持久化修复循环

候选按 Task 从 1 到 3；`candidate_no` 是新候选已持久保留的槽位，Attempt 重启不新增槽位。每个候选记录父候选/基准、完整归一化 patch（始终相对 base_revision）、candidate tree hash、生成配置版本、验证版本、当前状态。验证结果关联独立 verification_id，保存原始结果 Artifact 与摘要。恢复时在同一候选槽位继续；崩溃造成的模型重新请求不能突破候选上限，也不能把未提交的新输出当已确认 patch。

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言 | 风险与边界 |
|---|---|---|---|---|
| D01 候选持久模型，3–4h | H01/H02 | 新 `deploy/alembic/versions/0002_repair.py`、`tests/integration/test_repair_schema.py`；扩 `core/contracts/repair.py` | Task+candidate_no 唯一且 1..3；patch 字节/hash/验证版本和结果可持久；外键链接 Task/Step/Call 且不破坏 0001 数据 | 只增量迁移；不改旧迁移，不重建库；patch/日志大小沿用配额或显式新增配额 |
| D02 候选分配与生成 Action 原子绑定，3–4h | D01 | 新 `runtime/storage/repair_store.py`、`tests/integration/test_candidate_allocation.py`；局部改 `runtime/storage/store.py` 的 prepare 接口实现 | 模型调用前锁 Task 并检查 fence，分配槽位与生成 Action 同事务；事务回滚不耗槽/不留孤儿；并发/恢复同槽同 tool_call_id，第四候选拒绝 | 复用 Store 当前连接事务，不用“先写候选再 prepare”两次提交拼原子性 |
| D03 patch/验证 checkpoint 提交，3–4h | D02 | 改 `runtime/storage/repair_store.py`、`runtime/storage/store.py` 的 checkpoint 接入；新 `tests/integration/test_repair_checkpoint.py` | patch/hash 或验证结果与 Step/Ledger/Session 一起提交；提交故障全部回滚；旧 Attempt 不能覆盖新候选或验证结果 | 不将任意工具输出 SQL 化；限定 DTO 校验，验证工件受配额保护 |
| D04 任务请求与授权，3–4h | D01；独立契约评审 | 改 `core/contracts/task.py`、`core/contracts/execution.py`、`api/main.py`、`runtime/storage/store.py` 请求重建点、`runtime/security/policy.py`；新 `tests/integration/test_repair_admission.py` | 审核旧请求仍可用；code_repair 只能引用已批准 case_id，不能传 repo 路径/镜像/凭据；幂等/会话排他/预算仍原子 | 保留旧 TaskRequest 外部兼容，通过显式 discriminated request 解析扩展；不是开放任意仓库 |
| D05 节点结果与上下文接入，3–4h | D04、P01、M02 | 改 `core/nodes.py`、`core/kernel.py`、`core/middleware.py`、`runtime/orchestrator/worker.py`；扩 `runtime/context/repair_context.py`；新 `tests/repair/test_task_binding.py` | 受信任务绑定提供 observation/result/Artifact 投影，工具不能覆盖控制元数据；审核仍三步/可选发布；修复使用独立步上限，checkpoint 仍最后完成值 | 仅抽出硬编码绑定，不改事务控制流；审核 ContextEngine 不强塞修复字段 |
| D06 修复工具和验证无缓存，3–4h | H06、D03/D05 | 改 `runtime/tools/registry.py`、`runtime/tools/invoker.py`；新 `runtime/tools/repair_tools.py`、`tests/repair/test_repair_tools.py` | 所有修复工具先授权/校验/prepare/dispatch；验证缓存关闭且不读 document.fetch 缓存；每次 workspace 从固定基准+PG 已提交 patch 重建，旧容器丢失无影响 | 新 Harness backend 不扩大普通审核 Docker 的镜像白名单；重放验证会产生新执行记录但保留原逻辑调用 |
| D07 有界候选状态机，3–4h | D06、P03（Fake 路径可先完成） | 新 `runtime/planning/repair_planner.py`、`tests/repair/test_repair_loop.py`；改 `core/nodes.py` 注册白名单 | PASS 停止；真实测试 FAIL 才允许下一候选且最多 3 个；恢复不加 candidate_no，同一 Call 复用，新候选各操作新 ID | INVALID/BLOCKED 不生成假 PASS；耗尽候选为业务 UNREPAIRED，不伪装 Runtime 故障或成功修复 |
| D08 独立 Worker 恢复矩阵，3–4h | D07、R00/R02 | 新 `examples/repair_process_scenarios.py`、`tests/process/test_repair_recovery.py` | 在候选分配后/patch 提交后/验证提交后精确屏障崩溃，原槽恢复；删除本轮已核实归属工作区后仍重建成功；Step/Call/Attempt/fence/候选关联与三次上限成立 | 若三个窗口无法在半天内覆盖，先拆 3 个 ≤2h 子包；禁止降低断言或用同进程 Fake 冒充 Worker 恢复 |

D05 的步预算必须依据批准的有限状态机推导，包含最多三轮生成/验证及终结；不得把 candidate_no 当 checkpoint。D07 中 Runtime SUCCEEDED 表示流程完成，业务 outcome 用 REPAIRED/UNREPAIRED 等独立枚举；验证不可用按明确失败/阻塞原因报告，不能通过 outcome 掩盖运行故障。

### E：Evidence 与评测

| 包 / 时间 | 依赖 | 未来新增/修改文件 | 验收断言 | 风险与边界 |
|---|---|---|---|---|
| E01 证据关联与只读导出，3–4h | D03、D06 | 新 `runtime/observability/repair_evidence.py`、`tests/integration/test_repair_evidence.py`；改 `runtime/storage/store.py:diagnostics`、`api/main.py` 既有诊断输出 | candidate/patch/tree hash/验证版本/结果能关联 Task/Attempt/Step/Call/Ledger/Audit；字节可下载且 hash 可重算；Runtime status/business outcome/gate verdict 分开 | 不从日志恢复；不泄露模型凭据/黄金 patch；原 Artifact 唯一约束需按不同 Step 或新增验证表合理关联 |
| E02 固定任务集与盲测划分，3–4h | H06、M02 | 新 `examples/repair_cases/suite_manifest.json`、原创小型 case 目录、`docs/repair_evaluation_protocol.md`、`tests/repair/test_eval_split.py` | 首期固定小集（例如 2 debug + 2 holdout）及版本在调试前锁定；黄金 patch 与 prompt/context 导出隔离；禁止调试后重新分组美化结果 | 每 case 需基线失败和人工 patch 验证；若制作超过 4h，按每 case ≤2h 再拆，先冻结 split 再调参 |
| E03 确定性可靠性实验，3–4h | D08、E01/E02 | 新 `examples/repair_reliability.py`、`tests/process/test_repair_reliability.py`；新 `reports/<run_id>/reliability.md` | Fake 固定序列重复时实际独立进程故障计数可核验；不重复逻辑副作用、不超候选数；缺失/跳过/失败逐次保留 | 先小批精确窗口实验；270次/10或30次需独立排期和实际运行，不能预填通过率 |
| E04 真实模型修复评测，3–4h | P03 真实可用、D07、E01/E02、P0 通过 | 新 `examples/repair_evaluate.py`、`tests/repair/test_eval_aggregation.py`；新 `reports/<run_id>/repair-evaluation.md` | 固定模型/参数/prompt版本/manifest/镜像/套件且记录每候选；成功分子仅独立 Harness PASS，失败与 BLOCKED 明列分母；debug/holdout 分列，不查看黄金 patch | 实际后端无凭据时 BLOCKED，不能拿 Fake 补样本；小样本不作泛化/生产可靠性承诺 |

新增包目录按需加入普通 `__init__.py`，须与真实实现同包提交，不单独造空目录作为完成量。表中历史问题修复与新增能力分别排期，不全部塞入原 W7；整个计划显著超过半天，各包是独立可验收切片，不能以一次“大增强”提交。对跨多文件的 D04/D05，开始实施前先核定规模；若超过半天，分别按“请求解析/授权集成”和“Kernel 投影/Worker 绑定”拆成独立补丁。

## 执行顺序与退出门槛

顺序为：补齐方案/环境 → R 系列 P0 → H01–H06 独立 Harness → M/P 的有限接入 → D 持久修复循环 → E 两类评测。H01–H03 的离线开发可以不等 Docker，但真实运行验收和模型成功率不能绕过 H06/P0。依赖可用于排期，不要求本轮并行代理或实际执行。

下一项最小增强是 **H01 固定 CaseManifest 的完整输入校验**（2–3h），接着 H02 独立 Verdict。先把“什么算一次有效验证、何时绝不能 PASS”固定下来，人工正确/错误 patch 就能检验验证器；再接模型，才能区分模型没有修好与测试被跳过/篡改/损坏；最后接恢复循环，才能防止候选重算、旧 PASS、旧容器文件造成假成功。

本轮计划不是功能完成证明。环境授权、缺失附件和指定后端仍分别保留 BLOCKED/MISSING；没有自动修改冻结语义、没有启动下一工作包。
