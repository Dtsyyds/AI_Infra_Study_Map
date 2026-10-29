# 简历项目三项研究

2026-10-26；run=20261026T070223Z-three-track-research。目标是补上真实修复闭环、可解释的可靠性对照、本人可证明的贡献。本文为研究协议，实际结果以同run原始数据/报告为准。沿用不付费、不推送授权。

## R1 单个dev真实模型闭环：先完成离线准备

入口：`python -m examples.model_smoke_plan --case case-01 --output reports/<新run>/model-smoke-plan.json`。只做本地数据校验并冻结Manifest、镜像、runner、测试套件、提示词和Schema哈希；拒绝holdout，不读取模型密钥、不调用网络，输出不可覆盖。

首轮只允许一个dev Task，loop策略最多3个候选。审批草案为3次请求、每次最多2048输出token、全局100000本地token预留单位；这不是已经批准的预算，也不是金额报价。真实运行时必须新建预算ID，未知结果的预留不退款，实际token缺测不填0。当前实际开关false、请求/token预算0。模型名称和账号兼容性待确认，不默认选用最新或最贵模型。

2026-10-26按[OpenAI Structured Outputs官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)核对：Responses使用text.format；strict对象需要完整required及additionalProperties=false，仍须处理refusal/incomplete并在本地校验。当前Patch Schema通过这两项本地检查；选定模型的Schema关键字、temperature、输出上限和账号支持仍需核对及真实smoke，不能用MockTransport通过代替。已有Adapter无自动重试、无Fake回退，不因本次研究擅自换后端。

授权后的顺序：当前独立Harness qualification→冻结模型/采样/预算→单个case-01 dev任务→保存每次真实反馈/候选/当前可信Verdict→保存真实provider、响应ID、usage与最终Artifact字节/hash。成功需要SUCCEEDED、DELIVERED、当前候选PASS与真实响应证据同时成立。若未修好，如实记录；不注入gold patch，不为了演示伪造第一轮失败，不用单个案例宣传修复率。72任务矩阵另行授权。

## R2 可靠性对照：一个明确的跨HTTP提交窗口

冻结问题：外部服务已经提交效果、HTTP结果尚未返回时SIGKILL调用进程。外部效果在独立事务中提交，父控制器通过已提交屏障确认时机；不依赖固定sleep猜时机。

| 组 | 恢复信息和策略 |
|---|---|
| volatile_key | 首次key随进程丢失，替代进程使用新key重发 |
| stable_key_only | key先fsync到文件，替代进程读取原key重发；没有ledger与UNKNOWN策略 |
| runtime | 当前持久Action/Ledger/租约与按效果类别恢复；unsafe先NEEDS_REVIEW |

三组使用相同报告、相同独立本地业务服务，分别测试服务支持幂等与unsafe两种条件。固定key组用于检验外部服务幂等的贡献，避免把去重能力全部归因给Runtime。它是受控机制消融，不是LangGraph/Temporal等框架排名，也不是生产业务或完整代码修复吞吐测试。

预注册样本：每组×每种服务各1个无故障控制，共6；每组×每种服务各5个故障重复，共30；总36。故障组轮换执行顺序。每个试验有独立Task、key、外部请求和效果计数；直接调用组的Task仅作为实验身份和清理载体，不使用Runtime恢复。配额按自然窗口等待，不重置配额。

主要指标：外部实际请求数、效果数、额外效果数、调用方自动完成报告、NEEDS_REVIEW与显式测试操作员确认次数。unsafe组的人工确认是可信测试控制器检查独立效果后的模拟操作员动作，不是假称无人干预恢复。汇总中的automatic_successes只数调用方完成声明，必须同时看duplicate_effects；重复效果的完成声明不算正确业务结果。

假设：无故障时三组均一次效果；崩溃时新key组有重复风险；稳定key依赖外部去重；没有幂等保证时Runtime应保留UNKNOWN并停止自动重发。支持或不支持假设均保留原始记录，不筛掉失败。五次重复仅确认固定时机可复现，不估计随机故障概率；不跨组比较恢复耗时，因为租约等待和人工确认的语义不同。

入口：`python -m examples.reliability_comparison --repeats 5`。强制模型关闭、预算0；存在输出目录则拒绝覆盖。输出plan/raw/events/summary以及各独立子进程日志，实际外部事实来自arc_business，不依赖Runtime自报dispatch_count。

## R3 本人独立修改

任务书见[观察日志诊断练习](../exercises/observation_diagnosis/README.md)。代理只提供问题、合成输入和验收要求，不实现命令、不代写你的测试或结论、不代签掌握。你应在自己的获准练习分支完成，保留先失败后通过的证据和一次设计复盘。研究是否完成与本人是否完成练习分开记录。

## R2 首轮观测结果

36/36记录完成，6个无故障控制均为一次请求/一次效果。30个故障样本如下；数字是本固定屏障的重复实验，不能当随机故障概率。

| 故障组（各n=5） | 服务支持幂等：请求/效果/额外效果 | 服务不支持幂等：请求/效果/额外效果 | 不安全服务的自动恢复 |
|---|---|---|---|
| volatile_key | 10/10/5 | 10/10/5 | 5次声明完成，但产生重复效果 |
| stable_key_only | 10/5/0 | 10/10/5 | 5次声明完成，但产生重复效果 |
| runtime | 10/5/0 | 5/5/0 | 5次NEEDS_REVIEW；之后5次显式测试操作员确认，无额外派发 |

独立只读审计核对36个唯一Task、72条实验事件、61次外部请求和51个效果；15个额外效果均在预注册直接调用对照组。本实验新增模型预留0。实际原始数据、独立业务表事实、子进程日志、汇总和审计位于`reports/20261026T070223Z-three-track-research/`。

可支持的结论：稳定幂等键的去重效果依赖外部服务契约；在本次不支持幂等的已提交效果窗口，Runtime通过UNKNOWN/NEEDS_REVIEW避免自动重复派发，代价是需要明确对账。本轮不支持“任意副作用exactly-once”“全自动恢复”或“优于某个主流框架”的结论。
