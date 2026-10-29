# Agent Runtime Core：求职导向自主实施总任务书

版本：job-delivery-v2 / 2026-10-24。
执行对象：独立参考工程 agent-runtime-core-reference。
项目定位：Agent Runtime Core — Reliable Coding Task Execution。
主目标岗位：2027 届校招 / 实习的 Agent Infra、Agent 平台后端、Agent Runtime / Harness；兼顾 AI 应用后端、Agent 评测工程。
目标地区：北京、上海、深圳、杭州。项目交付沿用本项目约定的 2026-12-17，不因本任务书重新起算十周。

## 0. 本次授权与工作方式

你是本参考工程的实施负责人。现在授权你在既定范围内实际修改代码、补测试、修 Bug、运行实验、制作交付证据。不是只做核查或写计划，也不是只实施 H01 就停止。

工作方式：自主拆分 → 小步实现 → 自测 → 修复 → 集成 → 形成最终验收包。不要在每个 Hxx 结束后请求用户确认。内部计划、技术选择和测试修复由你负责；ChatGPT 只在最终交付后复核代码与证据，不参与逐片批准。

允许中断请求的情况仅包括：系统权限/外部服务凭据/付费预算/不可逆数据操作/真正的根路径歧义。把这些需求集中写入 blockers.md；继续完成不依赖阻塞项的安全工作。不能用自动化名义越权，也不能虚构已经在后台执行。会话或执行环境终止时，保存精确的 resume.md；下一次实际启动后再续做，不声称会自行跨会话运行。

本文件是自包含实施契约。不依赖上一轮缺失的“增强方案附件”。本文件仅替代“上一轮只能新增文档、不能改实现”的阶段限制；不替代 AGENTS.md、工具权限和系统安全限制。

## 1. 已知基线与事实等级

用户提供的最近复核摘要（不是你本轮重新跑出的结果）：
- 单元测试 17 passed。
- P0 未通过。Docker socket 权限不足，A/C/F 完整链路未运行。
- 缺少增强附件、Git commit（当时不是 Git 仓库）、本轮完整链路 Task ID、真实容器终止证据、最终 Artifact/hash。
- 已发现旧验收 Task ID 不一致、Artifact 证据不足，以及容器检查失败可能被误判为“已清理”。
- 83 个已采样文件 hash 未变，只证明已采样范围，不能宣称全仓库未变化。
- 已新增：docs/enhancement_baseline.md、docs/enhancement_plan.md、reports/20261024T135237Z-enhancement-p0/p0_revalidation.md、同目录 workspace-changes.json。
- 下一项原计划为 H01：CaseManifest 输入校验，然后独立 Verdict；二者目前不能算已实现。

先读当前参考工程实际源码、上述报告、AGENTS.md、architecture.md、design_coverage.md 和最新 acceptance。不存在的材料标 MISSING，不能编造其内容。旧报告保持原样，后续以新 run_id 出具更正/复核，不覆盖历史失败。

现有参考实现的已知语义，应在源码中确认后保留：
- Ledger 使用 PREPARED / DISPATCHED / COMMITTED / UNKNOWN 等实际状态，不机械套回旧设计的名称。
- checkpoint 是最后完成的步骤号，下一步为 checkpoint + 1。
- Session 快照主要用于同一 Task 跨 Attempt 恢复，不把它宣传成已经具有跨 Task 长期对话记忆。
- PostgreSQL、MCP 的历史局部证据不能替代当前 Docker 或端到端证据。
- 不复用正式学习工程或 month01–month04 的任何代码，也不把参考工程完成算为本人已掌握。

## 2. 招聘要求是范围依据，而不是堆技术清单

本轮岗位研究截至 2026-10-24。只把以下已读取的官方招聘正文/官方索引可见字段当作证据，不把所有标题里含 AI Infra 的岗位当成同一类，也不把社招经验要求伪装成应届要求。

| ID | 岗位与适用性 | 可核验要求摘要 | 本项目转化 |
|---|---|---|---|
| J01 | 阿里巴巴 Agent Infra 工程师，2027 校招；职位 199907640058；页面更新 2026-09-03 | 状态与调度、沙箱、检查点、安全、API/开发工具、可观测、基础计算机能力、CI 与交付 | Runtime 主线 + 可调用 CLI/API + 真实恢复/资源限制 + 工程证据 |
| J02 | 小红书 Agent Harness 工程师-企业智能方向，2027 校招；22074；北京/上海/杭州 | 官网索引可见会话、任务、工具、持久化、Trace/Log/Metric/Event、生命周期 | 持久任务与可诊断执行；不声称已实现 Serverless 弹性 |
| J03 | 百度 上海-Agent Harness 研发工程师 J100779；官方标校招，页面日期 2026-07-21，未据此推定具体毕业窗口 | 状态、沙箱、反馈、执行约束、上下文交接与 compaction、长任务稳定性 | 恢复、可信反馈、Context/RepoMap、受控修复闭环 |
| J04 | 蔚来 座舱Agent Harness算法工程师 A13971；2027校招，北京/上海；发布日期未显示 | Agentic Loop、工具、会话、压缩、Hook、MCP、Python异步、质量控制 | 六层执行机制；语音/后训练不纳入项目 |
| J05 | 小红书 AI应用研发工程师-社区工程，2027校招；22021，北京/上海 | Tool/RAG/上下文、评测与Badcase、软件基础、交付和稳定性 | 一条真实模型任务 + 固定评测 + CI |
| J06 | 小红书 AI审核研发工程师-Trust&Safety，2027校招；22072 | 软件工程、关系型数据库、SQL优化、IO/多线程、可用性 | 事务/锁/连接池/异步取消/容量与SQL计划证据 |
| J07 | 百度 Agent算法实习生 J103894，北京；2026-07-29 | 可复现任务、失败归因、Harness与评测、Python；同时偏研究 | 只借鉴验证与可复现评测，不能自称覆盖其研究要求 |
| J08 | 小红书 大模型Agent开发工程师（AI Coding方向），2027校招；22079，北京/上海 | Coding Harness、代码语义与工程上下文、执行验证修复闭环、质量评测；同时要求大型工程理解 | RepoMap + 独立Verdict + 有界修复；小型仓库不冒充大型工程经验 |

官方原始入口（必须区分岗位日期与检索日期）：
J01 https://campus-talent.alibaba.com/campus/position/199907640058
J02 https://job.xiaohongshu.com/campus/position/22074
J03 https://talent.baidu.com/jobs/detail/GRADUATE/74d83772-1bd0-42b9-8cc5-69eb45696b62
J04 https://nio.jobs.feishu.cn/campus/position/7673027279396161801/detail
J05 https://job.xiaohongshu.com/campus/position/22021
J06 https://job.xiaohongshu.com/campus/position/22072
J07 https://talent.baidu.com/jobs/detail/INTERN/70a8d8a6-5aff-47ad-88c8-24aa47ab1812
J08 https://job.xiaohongshu.com/campus/position/22079

部分官方页面正文依赖动态加载，研究可能只能取得官方索引摘录；没有显示的学历、年限、日期、在招状态一律未知。上述是定向岗位样本，不是全市场统计，不承诺所有岗位仍有名额或用户符合全部要求。不要为了“全面”把高年限、高强度训练或多租户云平台要求全部塞进本项目。

创建 docs/job_requirements_matrix.md：每项“官方要求 → 项目能力 → 源码符号 → 可复现测试/run → 可说的简历结论 → 未覆盖边界”。框架认知、OS/网络/数据库、算法、模型基础和口头表达作为个人能力留白，不能让生成代码替用户通过面试。

## 3. 最终交付目标与禁止扩张

保留已有六层 Runtime，新增主展示 code_repair：固定小型 Python 仓库的受控代码修复。document_review 继续作为原始链路回归，不再新增另一条复杂业务。

目标链路：
持久受理 → 冻结 CaseManifest → 基线缺陷验证 → RepoMap/受限上下文 → 真实模型提出补丁 → 路径/范围校验 → 候选持久化 → 新容器可信测试 → 独立 Verdict → 最多三候选修复 → 最终补丁与证据。

必须同时证明两类事：
A. Agent 能根据真实反馈产生候选并完成至少一个真实模型修复案例。
B. Runtime 在进程退出、旧租约、取消、未知副作用等情况下维持不变量。
用 Fake 验证 B，用真实模型验证 A，两者不能互相冒充。

不重写已有六层、不新增第七层“系统”、不为做架构而拆微服务。仍用 PostgreSQL 唯一运行时持久化；现有 Docker/MCP/Prometheus 复用并修复，不另起第二套执行系统。

不增加：Redis、Outbox、K8s、Operator、Ray、服务网格、通用工作流引擎、多Agent协作、Agentic-RL、模型训练、CUDA/Triton开发、多框架重写、MCP工具市场、生产级SSO/RBAC、强多租户沙箱、新前端或复杂大屏。
JD出现这些词不改变范围，只在差距文档中记录。不得因岗位提到Go就把已验证Python后端整体重写。

## 4. 自主执行、版本与权限

### 4.1 工作区
只修改已确认的 agent-runtime-core-reference；不碰正式学习工程、机器人项目或旧 month 目录。读取实际根路径和父目录Git状态，不能在不明目录里初始化仓库。

若确认参考工程是独立目录、其自身及父级均无管理它的Git仓库，本任务授权在该目录 git init，先建立 .gitignore 并扫描排除秘密/venv/大数据/测试产物，再保存基线。使用已配置的真实本地身份；不伪造作者、不修改全局身份。缺少身份时记录最小人工动作，先以全源文件hash快照保证可追溯，不能编造 commit。

允许本地小步提交，不推送、不建PR、不修改Git远端。已有用户改动先记录，禁止 reset --hard、git clean、粗暴覆盖。

### 4.2 Docker与外部阻塞
检查已有且获准的Docker context/daemon、socket、API可达性、镜像与可用控制器。不能 chmod 666 socket、擅自改用户组、sudo提权、开放不认证TCP daemon、privileged容器或挂载宿主socket给工具。

只允许使用当前已授权环境；权限变更、安装新daemon等由用户批准。需要权限时集中输出 blockers.md 的环境事实、根因、风险、最小动作与重试命令。

P0阻塞时继续H01/独立Verdict等纯数据校验、单元测试和不涉及候选执行的工作。不得在宿主运行Agent候选代码当替代沙箱，不得把Docker Mock计作通过。P0未过时禁止最终overall PASS。

### 4.3 模型与费用
使用一个可替换的真实LLM Adapter，优先沿用可用后端与锁文件。没有用户明确授权的模型后端和正的费用/token/request预算，不批量发付费请求；先完成Fake与录制响应契约测试。单独集中列出 provider/model/secret变量名/预算需求，不索取密钥明文到日志。

即使机器上有密钥也不自动代表允许无限调用。保留一次请求最大tokens、单任务调用/候选上限、全局调用与费用或token硬上限；重试也扣预算。定价无法确认时不编造金额，只报告用量与价格未知。真实请求不得默默回退Fake。

### 4.4 连续实施记录
创建并维护 EXECUTION_PLAN.md、TASK_STATUS.json、DECISIONS.md、blockers.md、resume.md。每步分别记录 implementation_status 与 validation_status；written/implemented 不等于 verified。

按依赖自动推进，不需要用户逐项验收。环境阻塞可跳过依赖它的运行阶段继续安全分支；最后明确 PARTIAL/BLOCKED，不“全部完成”。

## 5. 实施路线：按结果而不是空目录推进

允许根据现有 docs/enhancement_plan.md 自主拆分文件和小任务；H01保留为CaseManifest，后续编号以现有计划为准，建立映射不覆盖已有记录。以下G0–G7是交付门，不要求每门四小时；不得虚报工时。

### G0：修复可信基线与P0（先做；H01可在环境阻塞时并行）

1. 复现17个历史单元测试，记录本轮真实结果，不能沿抄旧数。
2. 补齐版本、依赖、完整源文件hash、测试集合、run_id；对旧Task ID不一致做明确差异记录。
3. 修复“容器检查错误等于已清理”：daemon不可达、权限不足、超时、解析失败均为 UNKNOWN/ERROR；只有成功与daemon交互且明确不存在才可认定ABSENT。必须区分存在且停止、存在且运行、明确不存在、无法判断。
4. 重跑A/C/F：正常审核最终报告；已提交步骤退出后新Worker恢复至最终报告；实际容器启动后超时且终止/归属清理。
5. 证据含Task/Attempt/tool_call、真实MCP读取计数、容器ID/标签/启动标记/退出与检查结果、Artifact字节及SHA-256。不能以局部publish故障替代整条链路。
6. 若发现真实事务/租约缺陷，允许最小修复并新增能复现旧错误的测试。不能删除旧断言以获取绿色结果。

G0门：原有核心回归通过；A/C/F端到端真实验证；清理不能把未知当成功。阻塞时保留原因、继续H01但不得关闭G0。

### G1：H01 CaseManifest + 独立Verdict（不先接模型）

Manifest是可信测试资产，不接受Agent生成/覆盖。至少字段：schema_version、case_id、base_revision或明确的snapshot_hash、repo_content_hash、image_digest、test_suite_hash、允许源码路径、禁止路径、预期测试ID、基线预期fail/pass集合、可信测试argv、资源/步骤/时间预算。

严格拒绝：缺失/多余不允许字段、非法类型、重复ID、空测试集、路径逃逸/绝对路径/软链接逃逸、未批准镜像、未批准命令、非法预算、不一致版本。NewType不承担运行时校验。case基准未使用Git时只称snapshot_hash，不能假造commit。

建立Independent Test Harness，可直接接收人工PreparedCandidate，不依赖Planner。返回PASS/FAIL/ERROR/NOT_RUN/INCOMPLETE及明确reason_code。

PASS同时要求：当前manifest、candidate_hash、verification_run_id/随机关联nonce、image/test集/仓库hash全部匹配；可信runner确实运行；预期test IDs实际收集并执行；必要测试无skip/xfail/缺失；回归测试无退化；报告完整；受保护文件未变；没有资源异常/超时/陈旧结果。

必须验证负例：空测试；全部skip；缺报告；损坏/截断报告；旧候选PASS；另一任务PASS；退出码与报告矛盾；配置/测试篡改；越界补丁；超时但留存PASS文件；nonce或测试集hash不匹配。错误候选应FAIL，测试基础设施问题应ERROR/INCOMPLETE，不把所有问题混作模型失败。

验证结果来源不是模型的“已完成”。来自候选容器的报告仍是不可信输入：元数据与可信运行记录交叉核对。此教学边界不等于抵抗任意恶意Python代码篡改同一进程pytest运行时；必须写入威胁模型，不能夸大。

G1门：原始缺陷可复现，人工参考修复可通过，所有规定误接收样例不产生PASS。gold patch不得进入Agent/模型上下文或可读工作区。

### G2：受控候选与可信沙箱执行

一个task_type=code_repair，一个固定小型Python仓库，最多3个候选（首次+两次修复），每候选最多3个批准源码文件、200行变更、32KiB补丁。限制可配置但不能由模型扩大。

只允许修改业务源码。禁止修改tests/conftest/pytest配置/依赖锁/平台代码/权限/迁移/凭据；拒绝绝对路径、..、软链接/文件类型逃逸、补丁超限及非文本修改。脚本参数使用受控argv，不接受模型任意shell命令。

每次从固定基准+已持久补丁重建全新工作区；没有上一容器残留作为事实源。测试镜像digest固定，依赖预装，工具容器无网络、非root、只读根、受控tmpfs/workdir、cap收缩、CPU/内存/PID/墙钟限制，不传平台secret/socket。

工具容器退出前保留必要退出/资源/日志证据；不能仅杀Docker CLI就称进程停止。遗留清理确认标签、task/attempt归属、当前租约与平台记录；无法查询数据库/daemon时保守保留并报告UNKNOWN，不误杀活Attempt或无关容器。

允许可信编排进程管理容器；不能在宿主导入或执行候选代码。语法检查等需要执行候选的动作也在受控环境。

G2门：正确/错误补丁、测试篡改、资源超限与清理路径均真实运行；只用Mock不得通过。

### G3：RepoMap、Context、真实模型

RepoMap仅支持Python AST，提供文件、模块、符号、签名、导入和直接语法引用，动态调用标UNRESOLVED，不宣传完整运行时调用图。引用绑定base/hash和路径行范围；允许读取的代码范围受策略约束。

ContextBundle固定为任务目标、允许修改范围、相关代码、已实际失败的测试摘要、上轮候选/结果、剩余预算。Protected Facts包括base/test/image/hash、candidate身份、policy、deadline和未决效果；压缩不能改写这些字段。

真实模型只输出严格结构化动作/补丁。校验拒绝多余权限字段、任意镜像/命令和越界路径。重试只处理明确暂时错误并有上限；对不确定模型计费不承诺exactly-once。LLM调用在可信进程、密钥不入容器。

保留DeterministicPlanner用于可靠性实验；真实Adapter用于任务效果实验，记录provider、model实际标识、采样参数、prompt_version、输入/输出token（缺测写null）和调用错误。

不在本任务中盲信先前宣称的SDK版本。先读现有lock、实际安装版本和官方API文档，兼容即可保留。确有必要升级时记录ADR和兼容回归，不悄悄替换MCP/模型协议。

G3门：Fake与真实路径严格分离；至少一次获准的真实模型任务有请求、结构化候选与验证证据。缺凭据/预算为BLOCKED，不能宣称真实模型修复。

### G4：有界修复与持久恢复

区分task_id（逻辑任务）、candidate_no（新方案）、attempt_id（Worker尝试）、tool_call_id（逻辑工具动作）、verification_run_id（一次验证运行）。

恢复同一已提交候选不增加candidate_no、不重新请求模型；新候选新动作必须有新逻辑调用身份，不能覆盖旧Ledger。测试中断后重跑要记录新的物理验证run，但维持正确的逻辑工具身份与去重语义。

持久保存patch Artifact、candidate_hash、manifest/test/image版本、已确认Verdict和上下文；候选计数/预算跨重启不清零。候选自测在无网络无外部挂载约束下才能按受控可重算执行处理；真实外部发布继续遵守Ledger未知效果语义。

三类结果分开：
- Runtime：既有SUCCEEDED/FAILED/TIMED_OUT/CANCELED/NEEDS_REVIEW等；
- 修复业务：DELIVERED/UNRESOLVED/REJECTED_POLICY等；
- 验证：PASS/FAIL/ERROR/NOT_RUN/INCOMPLETE。

DELIVERED只能依据当前候选有效PASS；模型自述不能终结为修复成功。环境错误不能算修复率中的普通模型失败而隐去，也不能算PASS。

保留原持久语义：202在COMMIT后；认领+Attempt同事务；Plan+Ledger同事务；Ledger+Audit在派发前；Checkpoint/Artifact引用一致；租约数据库时间；旧写回拒绝；取消不等于外部撤销；外部无可靠幂等/对账能力时UNKNOWN；reconcile验证版本、deadline、取消与全部未决效果。

G4门：patch提交后退出、测试结果提交后退出、测试运行中退出、旧Worker恢复均有独立进程证据；已完成逻辑步骤不重放、不重置候选预算。

### G5：后端工程与开发者入口（强化招聘基础项，不做新平台）

1. 延续实际API路径，OpenAPI准确；CLI支持提交/状态/取消/等待或事件观察/取artifact/诊断，调用真实API，不直接读写数据库绕过权限。已有命令合理则扩展，不重命名整仓库。
2. 输入严格校验、body/输出限额、稳定错误码、超时与取消；重复请求/冲突/配额拒绝契约测试。跨principal查询Task/Artifact/缓存拒绝。
3. API和Worker有界并发、有界连接池、有界工具等待；不得阻塞事件循环，不依赖进程内Semaphore宣称跨Worker配额。配额预留/释放和Task事实一致。
4. 关键SQL的索引与计划证据：claim、pending action、diagnostics各选代表查询，在可重复合成数据规模上记录EXPLAIN计划、锁等待/连接使用。没有测得改善就不写“优化提升”。不为压测永久修改数据库全局配置。
5. 优雅退出停止新认领、处理活任务/租约、回收已授权资源；强杀恢复另测。SSE最新快照不冒充事件溯源。
6. health/live、health/ready与实际依赖一致；依赖锁、迁移空库/升级回归、Ruff/mypy/pytest、可信CI配置。没有远端运行记录只能称本地CI等价命令通过。
7. 为LangGraph/PydanticAI等招聘常见抽象只写“本实现对应关系与取舍”短文，不开发第二套框架；所用框架版本与认识必须核对官方资料，不声称已有使用经验。

G5门：第三方客户端能够操作任务；过载、断连、身份隔离、连接释放、SQL计划与异步行为有测试或测量；不写海量生产规模承诺。

### G6：评测、可观测性与可比较结果

先建立12个小型固定缺陷Case，覆盖边界条件、API/序列化、数据转换、异步错误处理。每个原始缺陷可复现，参考修复可通过；数据来源/许可证明确。平台生成的合成任务如实标注，不冒充公开大型benchmark。

预先冻结4个dev、8个holdout；gold patch只给可信校验器。不同分组尽量避免同模板变体泄漏。开发Agent知道构造过程不构成独立第三方盲评，须明示局限。holdout不能用来反复调Prompt后再当新鲜成绩。

真实模型三组：A无RepoMap单次；B有RepoMap单次；C有RepoMap最多3候选。锁定同一模型/温度/测试集/镜像/预算定义，报告额外候选和token成本。A/B保持输入预算可比；C额外计算开销不得包装成纯架构改进。

完整目标：8个holdout × 3组 × 3轮 = 72个真实任务；该矩阵只能在明确获准的费用/调用预算内执行。不足则保存已运行结果，标实验INCOMPLETE/BLOCKED，不能虚构缺失样本。至少一个真实候选经可信验证DELIVERED才有“真实修复示例”的证据，低修复率可以如实交付，不为达标改黄金答案或挑选成功样本。

Runtime负载与模型效果分开：确定性任务1/5/10并发，各3轮、每轮30个独立任务，共270次提交。不通过重复Idempotency-Key伪造负载，不把202当任务成功。报告成功/失败/超时/取消/拒绝/NEEDS_REVIEW/仍运行的数量及分母。

故障最低重复：受理未认领与步骤后退出各10；外部幂等/可查询/不安全各10；旧Worker接管30；取消先/成功先各10及自由竞争30；审计失效10；错误摘要5类×2；容器正常/超时/资源耗尽/取消/进程强杀恢复各5。开发时先单次确定性；最终运行脚本自动重复，保留每次结果。不依赖sleep碰运气。

额外验证Verdict的空测试、skip、报告篡改、旧PASS重放、candidate错配等；关键误接收必须0。故障中出现的UNKNOWN可以是预期正确行为，不按任务成功率掩盖它。

记录队列、首次认领到终态墙钟、总耗时及恢复间隔，排队超时的执行耗时N/A。报告每轮n/N与P50/P95/最大值，不用快速拒绝降低P50宣传改进；小样本P95要注明不稳定。TTFT不当纯prefill，后端没提供prefill/decode不得猜测。

Trace关联Task/Attempt/Step/Tool/Candidate/Verification，Prometheus只固定低基数标签。Audit与Checkpoint不同，日志不能决定是否重试。

G6门：原始数据、配置、统计程序、错误分类与报告能相互重算；真实模型与Fake结果不能混表；性能改善只写实际同条件证据。

### G7：打包、复现与求职证据

完善环境/部署/迁移/演示/排障/停止流程。最终交付不是“已有很多代码”，而是可复现的一条主链路和能经受追问的证据。

文档至少：架构与事务；从请求到补丁阅读路径；故障演示；Threat Model；Verifier Contract；Job requirements matrix；已覆盖/未覆盖；Badcase；45分钟面试提纲；待本人完成的独立修改练习。

面试材料只写技术事实。简历草稿中每个数字关联run和原始数据；没有证据留空。不冒充实习、生产使用、公开benchmark成绩，也不把Codex生成代码冒充用户独立掌握。算法/OS/网络/数据库/模型知识与本人独立修改必须标NOT_ASSESSED，留给最终验收。

## 6. 不可放松的安全与数据边界

- core/contracts只放协议/DTO/枚举，core不能反向import runtime/api；AST测试持续通过。
- 不把异步对象、闭包、线程池或容器句柄当持久Checkpoint。
- 新Agent工具不能让候选改变principal、镜像、测试、权限、deadline、quota或验收规则。
- 保留Artifact BYTEA上限：单件2MiB、任务8MiB、平台逻辑512MiB，事务性检查。核心验证证据超限不允许静默截断；日志预览可显式截断但不可替代完整必需证据。
- 文件hash证明一致性而非真实性；完整测试通过也不是任意恶意代码强隔离的证明。
- 同库事务不等于跨HTTP原子；Lease/fence不撤销已发出的远端请求。
- 报告不能伪造Git commit、真实模型名/费用/调用ID、镜像digest或容器结果。
- 不使用全局Docker prune、删除未知卷、读取生产secret、自动推送/PR/部署生产。
- 所有demo网络默认loopback，模型受控外联仅可信进程，测试容器无外网。

## 7. 最终交付包与验收接口

写入 delivery/<UTC-run-id>/：

1. FINAL_REPORT.md：结论PASS/PARTIAL/BLOCKED/FAIL，列各G门和每项证据，不只列测试数量。
2. gates.json：每门implementation/validation状态、run ID、证据路径、阻塞原因。
3. source_manifest.json：实际commit或快照hash、dirty状态、代码/测试文件hash；未修改的正式学习目录边界。
4. environment.json：Python/PG/SDK/Docker/镜像digest/模型配置，脱敏。
5. commands.jsonl：命令、cwd、开始结束时间、退出码、日志路径，无secret。
6. tests/：JUnit或机器可读结果、原始stdout/stderr、marker分类、skip/blocked原因。
7. runs/：Task/Attempt/Step/tool_call/candidate/verification关联、独立外部effects计数、容器事件与inspect证据。
8. artifacts/：最终patch、结构化verdict、必要报告及SHA-256；与数据库导出元数据核对。
9. benchmarks/：72真实任务与270确定性任务分别的原始数据/未完成清单/配置/统计程序。
10. job_requirements_matrix.md、resume_evidence.md、interview_pack.md：项目与岗位映射及个人能力留白。
11. REPRODUCE.md：干净环境复跑单测、集成、容器、MCP、故障和真实模型的命令；需要授权的步骤显式注明。
12. blockers.md、resume.md、workspace_changes.json：剩余工作、确切恢复点、创建的服务资源和停止方式。
13. acceptance_bundle.zip或tar.gz：源代码/锁文件/迁移/测试/小型固定案例/原始报告/证据清单；排除env、secret、大镜像、无关文件。保留校验hash。

报告不能含本机API Key、数据库密码、个人简历联系方式等不必要敏感信息。受控备份/导出不得带无关库数据。

全套通过条件：G0–G7必需项实际验证，无隐藏FAIL、不可解释旧PASS、跳过核心集成或环境替代。没有Docker权限/真实模型预算则结论不能为全套PASS。

一次会话资源不足：保存已完成代码和每个检查点，报告PARTIAL；不得编造未来执行或仅留下“下一步应该做什么”。

## 8. 最后自查与开始动作

交付前自己做一次反例驱动审查：故意让每个可能误PASS的入口失败，确认最终报告不会误判成功；至少审查容器inspect失败、报告缺失、old PASS、API202误计成功、Fake混入真实组、旧attempt写回、审核被取消后仍DELIVERED等情况。

ChatGPT是最终复核方，你不能代表它宣布已验收。提交“可复核”，不提交“用户已经掌握”。

现在开始：
1. 确认参考工作区和权限，读取已有核查文件；
2. 建立版本/源码快照与持续执行计划；
3. 实施H01与独立Verdict，同时修复和复验P0；
4. 按依赖继续完成G2–G7，不等待逐片确认；
5. 最后返回验收包路径、真实结论、阻塞和唯一恢复入口。
