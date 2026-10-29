# 决策记录

- 2026-10-24：新主任务书取代旧文档限定，缺失旧附件不再阻塞。保留历史报告原件。
- Git自身及父级无仓库，已有本地身份；授权建立独立本地基线，忽略运行环境、报告、大数据与秘密，不设远端。
- 继续现有checkpoint/Ledger/Session事务语义，新增功能经现有Kernel/ToolRuntime/Store/Worker集成。
- 只有真实沙箱才能执行候选；数据校验、AST读取和报告反例单测可离线执行。

- 保留固定 Python 3.11.17 slim digest，使用 stdlib unittest 可信 runner；不增加镜像构建或 pytest 依赖。case snapshot_hash 不伪造 Git revision。
- v1 Patch 使用唯一 exact replacement；批准既有源码文件最多3个、200变更行、32KiB。可信平台校验后才能持久化。
- Verdict 状态遵循新主契约 ERROR/NOT_RUN/INCOMPLETE，不回写旧方案 INVALID。实际基线未运行时不生成候选。
- 模型接口沿用 httpx 锁文件直接 Responses REST，Fake 明确分离。没有真实授权/预算/密钥则不调用；不自动回退。预算预留持久化且未知调用不退款。
- 无需新持久层：repair_candidates/repair_verifications 和模型预算表置于同一个 PostgreSQL，候选/Step/Artifact/Session/Ledger原checkpoint事务一起提交。验证缓存关闭。
- 连接池每进程4连接/16等待/2秒超时，跨进程受理仍由数据库限额决定；实验等待实际受理窗口，不放宽配额。
- 容器 UNKNOWN 不等于清理成功；实际权限未改变。14个 Docker pytest skip 全部列 BLOCKED。
- 正式学习工程和 month 目录未读取/复制/修改。没有推送、PR、安装daemon、付费模型调用。
- 受控教学威胁模型不声称抵抗任意恶意 Python 修改同解释器 runner；hash只证一致性。外部最终验收与个人掌握均由独立复核方判断。

- 2026-10-25：继续用户授权的安全收尾；新run保留上一包不覆盖。Docker仍permission denied，不使用任何宿主候选替代。
- orphan判断改为True/False/None：仅数据库确认的过期Task/Attempt/fence关系才能清理，未知标签归属保留。避免把“查不到活Attempt”等同“已属于平台且失效”。
- Protected Facts补候选身份和未决业务效果；已有v1快照按原字段校验后升级，不改变checkpoint/Attempt语义。真实模型费用未知预留仍在model_calls/model_budgets，不能因空业务效果列表退款。
- trusted runner仅增加受信测试屏障，固定案例源码/测试/gold不改；Manifest与suite的runner hash随之更新。尚未有真实模型调用，不挪用旧PASS。
- 模型评测前必须当前独立Harness qualification；所有实际提交即使HTTP/Worker/Artifact观测失败也留记录。202/预算预留/模型成功响应分别计数。
- 框架映射只核对官方概念，不安装第二套框架、不虚称版本使用经验。迁移文档路径纠正为deploy/alembic/versions/0002_repair.py。

- 本轮最终151 passed/22 Docker skipped；旧14 skipped是历史记录不覆盖。停止自有PG/API/业务服务保留数据，新包不覆盖旧包。源代码自测完成不等于P0、真实模型或外部验收完成。

## 2026-10-26 免费验证与实际修复

- 用户明确只做不调用付费模型的验证。所有可信运行进程显式禁用模型、请求/token预算设零；不请求或读取真实密钥。
- 确认系统已授予dts Docker组资格，但执行宿主继承旧组。经工具批准使用sg docker启用已有组权限，无组成员/socket权限修改、无sudo、无daemon更换。
- 真实恢复暴露探针身份错误，改用既有demo-reviewer，保留独立session/task身份；绝不放宽策略。
- 导入依赖预设ERROR级root日志导致清理证据缺失。Worker CLI显式force配置INFO，并新增独立子进程反例测试。
- 服务/proc权限不足改为UNKNOWN，拒绝误报停止或覆盖PID记录；启动端口探针匹配服务的SO_REUSEADDR行为。
- 补活容器SIGTERM后确认ABSENT，再独立Worker恢复；与强杀后的orphan清理区别记录。
- 环境实际核验固定镜像digest，交付收集真实审核Artifact及Fake修复证据，保留本轮早期失败报告。
- 负载各轮等待既有受理窗口自然到期，独立记录等待，避免前一轮耗尽限流造成后续并发组全被提前拒绝；不改配额、不把拒绝混入成功延迟。

- 真实负载发现诊断API的Artifact元数据缺少sha256，驱动器因此拒绝将任务成功当成完整观测成功。已在同一数据库只读快照返回encode(sha256(content),'hex')，补PG字节/hash及驱动器匹配/错配回归。首次78条OBSERVATION_ERROR和1个中断任务独立保留；新270条数据位于benchmarks/load-attempt-2，不覆盖、不混合分母。

- 本轮最终：179 passed/0 skipped；P0、36 Harness、25容器、4恢复点、90+50故障通过；270负载结果SUCCEEDED 108、REJECTED 162。全部自有服务和容器已清理，PG数据保留。真实模型0调用；整体PARTIAL不代替外部验收。

## 2026-10-26 证据工具增量（20261026T031046Z-evidence-hardening）

本轮完成提交意图/受理身份持久记录、取消结果落盘、旧证据防覆盖、模型/负载预检状态分离，以及归档清单/真实JUnit计数/日志引用/被测源码再核对。112项单元及数据契约通过；Ruff/mypy通过；原完整包只读复核通过。完整运行基线仍是原179项与270负载，未冒称本轮全部重跑。下一阶段为独立配额内持续负载，然后在另有授权时做真实模型smoke。

## 2026-10-26 配额内负载

使用独立steady配置（1/2/3并发，各3×30）补足原1/5/10过载证据。保留活任务3、每分钟30次的实际配额和每轮自然窗口等待，不以拒绝耗时宣传性能。实验运行时模型关闭、预算0；新增只读verify_load从计划、日志、诊断时间和Artifact字节重算一致性。观察器强杀恢复作为单独探针，不混入270任务分母。

## 2026-10-26 三项研究取舍

可靠性比较纳入stable-key-only中间组，防止把下游幂等当成Runtime独有能力；unsafe进入NEEDS_REVIEW后测试操作员确认单独计数，不算自动成功。比较限于局部已提交效果窗口，不做性能/框架优劣外推。单dev模型请求预算只出待授权草案；个人贡献练习只准备规格与合成输入，不替本人完成实现或声明掌握。

## 2026-10-29 产品化启动

- 用户同意发展“可靠代码修复工作台”，按M1–M6推进。扩展前端、项目模型、审批和项目权限；沿用已有Runtime与事务边界。原无前端限制由本次用户授权更新。
- 本机8核16线程、14.99 GiB内存适合单任务开发；工作区只剩约6.6 GiB，先增加可用空间再安装大依赖。不修改配额、不删除原始证据、不下载本地模型。
- 新增只读开发预检，不耦合数据库在线状态，不加载.env或调用模型。开发资源提示与集成验收分开；没有检查或无法读取的项不能标READY。
- AI实现贡献与本人独立练习分别记录；练习契约、测试和实现继续由本人完成，产品推进不等于已掌握。
