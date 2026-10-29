# 招聘要求 → Agent Runtime Core 交付证据

检索日期：2026-10-24。
目标：2027届校招/实习；北京、上海、深圳、杭州；Agent Infra / Agent平台后端 / Runtime / Harness。

## 1. 研究口径

这是本轮定向查询的官方岗位样本，不是全市场普查或所有岗位的实时名额清单。只把公司招聘官网或公司使用的官方ATS当作要求依据。聚合站和个人文章仅用于找到线索，不作为最终岗位要求来源。

“网页显示的更新/发布日期”“搜索引擎抓取日期”“本次检索日期”是不同字段，不能相互替代。检索可见不代表今天新增，也不保证HR仍有HC。JS动态站点无法直接展开全文时，明确记录官方索引摘要范围，不推测缺失部分。

本文件使用短摘要和项目推论，不转载完整JD。不引用他人简历的成绩作为岗位要求或本项目结果。

## 2. 直接对应的官方样本

### J01 阿里巴巴：Agent Infra工程师
- 官方入口： https://campus-talent.alibaba.com/campus/position/199907640058
- 类型：Alibaba 2027 Graduate Recruitment。
- 官方毕业范围：2026-11-01至2027-10-31。
- 页面更新：2026-09-03。
- 地点：北京、广州、杭州、上海、深圳。
- 获取情况：官方索引提供职责与Qualifications；直接页面动态加载，不能据空HTML推断下架。
- 要求摘要：执行与状态、沙箱与恢复、安全调用、开发者接口、可观测、OS/网络/数据库和至少一门语言、CI/交付。
- 我们的映射：G0/G4的持久恢复，G2沙箱，G5后端/API/CLI，G6可观测，G7交付。
- 未覆盖：大型云原生/Serverless、多租户、训练与规划算法；JD涵盖多个方向，不要求本项目实现所有方向。

### J02 小红书：2027校招 Agent Harness工程师-企业智能方向
- 官方入口： https://job.xiaohongshu.com/campus/position/22074
- 类型/地点：2027校招；北京、上海、杭州。
- 发布/更新时间：本次结果未显示。
- 获取情况：官方索引摘要，能确认题目、项目、城市和部分职责；直接全文抓取失败。
- 仅据可见职责：会话、工具和任务持久状态；运行生命周期；Trace/Log/Metric/Event及调试诊断。
- 映射：L3核心状态、L6可观测、任务诊断及恢复。
- 未覆盖：Serverless自动扩缩容；完整资格字段没有读取到，不补写。

### J03 百度：上海-Agent Harness研发工程师 J100779
- 官方入口： https://talent.baidu.com/jobs/detail/GRADUATE/74d83772-1bd0-42b9-8cc5-69eb45696b62
- 类型/地点：官方标校招，上海；未由本页确认具体毕业年限。
- 页面日期：2026-07-21，不称“10月新发布”。
- 获取情况：直接官方页面可读取职责；搜索索引还提供要求字段。
- 要求摘要：可控可验证执行、状态、沙箱、反馈和上下文压缩；理解模型与Harness交互；工程化交付和问题分析。
- 映射：独立验证器、有界修复、持久恢复、故障归因、RepoMap与Context。
- 未覆盖：大型真实产品规模、商业化结果、所有列出的框架经验。

### J04 蔚来：校招-座舱Agent Harness算法工程师 A13971
- 官方ATS： https://nio.jobs.feishu.cn/campus/position/7673027279396161801/detail
- 类型/地点：2027届校园招聘正式批；北京、上海。
- 页面日期：未显示。
- 获取情况：官方ATS索引包含职责和要求；本次直接页面展开失败。
- 要求摘要：Agent循环、工具、会话持久性、压缩、Hook与MCP；Python/异步和工程质量；能评判AI Coding输出。
- 映射：六层边界、异步工具和取消、真实MCP、Context、Verifier。
- 资格差距：岗位带算法、实时语音、后训练内容，不把受控Python修复项目包装成完整座舱/语音经验。

### J05 小红书：2027校招 AI应用研发工程师-社区工程
- 官方入口： https://job.xiaohongshu.com/campus/position/22021
- 类型/地点：2027校招；北京、上海。
- 页面日期：未显示；官方索引可读职责和任职资格。
- 要求摘要：模型接入、工具/RAG/上下文、链路调试、自动评测、Badcase；计算机基础、开发与测试交付。
- 映射：单一真实模型Adapter、固定任务集、错误分类、可复现CLI/API。
- 差距：UI和多端协作不在本项目范围。

### J06 小红书：2027校招 AI审核研发工程师-Trust&Safety
- 官方入口： https://job.xiaohongshu.com/campus/position/22072
- 类型/地点：2027校招；北京、上海、武汉。
- 页面日期：未显示；官方索引可读。
- 要求摘要：高质量稳定服务、编程与软件基础、IO/并发、关系数据库和SQL；Agent实践加分。
- 映射：SQL事务/锁/索引、连接池、过载拒绝、迁移与回归、工作流质量证据。
- 差距：JD涉及的JVM和中间件生态不等于这个Python项目已经覆盖；不为凑词新增框架。

### J07 百度：Agent算法实习生 J103894
- 官方入口： https://talent.baidu.com/jobs/detail/INTERN/70a8d8a6-5aff-47ad-88c8-24aa47ab1812
- 类型/地点：日常实习，北京。
- 页面日期：2026-07-29。
- 要求摘要：可复现复杂任务、失败归因、Harness与评测、Python和系统实现，同时涉及研究/搜索/学习方向。
- 映射：受控缺陷集、Verifier、统计与Badcase。
- 定位：相邻研究岗，不因写了一个Harness就称满足其全部要求。

### J08 小红书：2027校招 大模型Agent开发工程师（AI Coding方向）
- 官方入口： https://job.xiaohongshu.com/campus/position/22079
- 类型/地点：2027校招；北京、上海。
- 页面日期：未显示；官方索引可读职责与资格。
- 要求摘要：Coding Harness、代码语义和工程上下文、执行与验证修复、质量评测；程序分析与大型代码库理解。
- 映射：Python AST RepoMap、候选持久化、独立Verdict、有界反馈修复、固定评测。
- 边界：小型Python语料证明最小机制，不冒充大型客户端仓库、编译工具链或论文研究成果。

## 3. 补充对照与明确排除

### 百度 Agent后端研发工程师 J103882（社招）
https://talent.baidu.com/jobs/detail/SOCIAL/d1876313-6604-4e8d-bbcc-0dca5be4e51b
页面日期2026-07-29，北京。涉及Agent网关、服务链路、集成部署、运维与OS/网络/数据库，强调Go。作为后端工程能力对照，不计入用户2027校招直接适配样本，也不驱动项目改用Go。

### 百度 云原生Agent Infra高级研发工程师 J100117（社招/高级）
https://talent.baidu.com/jobs/detail/SOCIAL/4b326f6b-2c39-461c-ae64-69b4201c680e
页面日期2026-07-21，北京/上海。含云平台、虚拟化、强隔离、编排等更广职责。只帮助划清“单机Runtime”和“大规模云Infra”差别，不能把岗位高级要求都转成当前功能。

### 百度 代码智能体大模型架构实习生 J101404（研究/训练Infra邻近岗）
https://talent.baidu.com/jobs/detail/INTERN/397771ff-709c-42f5-9fd7-2e4112e0768d
北京，页面日期2026-07-21。涉及隔离执行、轨迹、训练/推理系统；资格显示在读硕士/博士及每周4天以上、连续6个月以上。不能只见“Coding Agent”就忽略时间和研究要求；不以此增加RL或GPU训练模块。

### 博世 大模型算法工程师(27届校招)_BCSC
https://jobs.smartrecruiters.com/BoschGroup/744000142551949--27-bcsc
官方ATS标题写27届，但资格正文写2026届硕士及以上，存在文本冲突。要求含Harness、评测、Badcase也含训练/微调。本轮只当相邻方向证据，毕业资格待公司确认，不列为已经确认的2027适配岗位。

### 腾讯官方Harness/质量岗位
https://careers.tencent.com/jobdesc.html?postId=2098252452842352640
https://careers.tencent.com/jobdesc.html?postId=2092600134906064896
前者取得官方索引中的Context/评测职责但本轮未完整展开标题与经验字段，故不补写年限。后者为基模生产平台QA，官方显示3年以上及2026-09-20更新；仅作端到端质量能力对照，不冒充应届机会。

### 未采用的证据
字节岗位找到多个第三方镜像，但本轮相关官方详情抓取不稳定，不用镜像的更新日期、薪资或“仍招聘”当已官方核实事实。没有查到不代表该公司没有岗位。

## 4. 岗位共性与项目取舍（设计推论，不是统计排名）

| 能力 | 样本支持 | 项目交付证据 | 个人仍需掌握 |
|---|---|---|---|
| 后端基础 | J01/J05/J06 | 事务、锁、连接池、错误与API契约、SQL计划 | 数据结构、OS、网络、SQL原理 |
| 长任务与状态 | J01/J02/J03/J04 | Lease/Attempt/Checkpoint/取消/UNKNOWN故障记录 | 一致性与副作用边界解释 |
| 工具与安全 | J01/J03/J04 | 真实MCP、权限拒绝、资源限制、可证明清理 | 容器威胁模型与权限理解 |
| 上下文 | J03/J04/J05/J08 | RepoMap、Protected Facts、压缩前后断言 | 静态代码分析局限、token预算 |
| 任务效果与可信评测 | J05/J07/J08 | CaseManifest、独立Verdict、三组对照、Badcase | 分母、偏差、成本与因果限制 |
| 运维和观测 | J01/J02/J05 | Trace/metrics/audit、Diagnostics、冷启动复现 | 问题定位和服务稳定性 |
| 端到端AI Coding交付 | J01/J03/J04/J08 | Codex执行轨迹、人工复核、失败修复与回归 | 独立审查/修改生成代码 |

工程主次建议：主攻Agent Runtime/平台后端；code_repair是展示系统行为的载体，不把求职方向改成训练算法。少量高质量完整证据比技术名词覆盖率更重要，这是基于用户时间与已有基线的设计判断，不是全市场量化结论。

## 5. 来源之外的技术参考

Docker权限边界（官方）：https://docs.docker.com/engine/install/linux-postinstall/
其中说明docker组具有root级控制能力；本任务书禁止无授权修改权限，不给工具容器daemon权限。

Codex执行工作流（官方）：https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex
其中说明AGENTS.md受识别，其他计划/上下文文件是可选择的工作约定。本项目EXECUTION_PLAN.md等是我们选择的交付记录，不宣称为Codex强制接口。

这些技术参考不能替代目标机器上实际版本、权限和执行结果。
