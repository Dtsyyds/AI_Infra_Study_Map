# 岗位要求与可复核实现映射

范围依据为用户提供的 `JOB_REQUIREMENTS_2026-10-24.md` 与 `CODEX_MASTER_PLAN.md`。岗位日期、适用人群与官方原始链接沿用该输入，不声称本次重新核验实时在招状态。官方索引摘要不等于完整 JD；深圳等目标城市不代表每个样本都有岗位。本项目规模不代表大型平台经验。

本轮 run：`20261026T021326Z-docker-free-validation`。下列测试/实验均可在验收包 source/ 和 reports/ 定位；“实现”不等于 G 门已通过。

| 输入官方要求 | 项目能力 / 源码符号 | 测试或 run | 可说的结论 | 未覆盖边界 |
|---|---|---|---|---|
| [J01 阿里 Agent Infra 2027 校招](https://campus-talent.alibaba.com/campus/position/199907640058)：状态/调度/沙箱/安全/CI | Store.claim/checkpoint；DockerBackend.inspect_state；CLI；本地 CI 配置 | tests/process、tests/docker/test_inspect_errors；reliability/raw.jsonl | 实现持久状态与有界执行规则，实测旧 Worker 拒绝写回 | 真实容器已验证；无 Serverless、强多租户与远端 CI 证据 |
| [J02 小红书 Harness 2027 校招](https://job.xiaohongshu.com/campus/position/22074)：会话/任务/工具/Trace/生命周期（索引摘要） | Worker、Kernel、TelemetryHook、Session、diagnostics | 独立进程 checkpoint/old_worker；repair-process/result.json | 同 Task 跨 Attempt 恢复，日志可关联持久身份 | 不支持跨 Task 长期对话、事件溯源、弹性扩缩 |
| [J03 百度上海 Harness 校招](https://talent.baidu.com/jobs/detail/GRADUATE/74d83772-1bd0-42b9-8cc5-69eb45696b62)：状态/沙箱/反馈/上下文约束 | RepairContext、model_context、judge、RepairPlanner | tests/repair；additional-faults 错误摘要；真实 PG 候选测试 | 实现受限上下文与独立质量门，未把模型自述当 PASS | 具体毕业窗口未知；真实模型闭环 BLOCKED；大型 compaction 未测 |
| [J04 蔚来 Harness 2027 校招](https://nio.jobs.feishu.cn/campus/position/7673027279396161801/detail)：Loop/MCP/Hook/Python异步 | Kernel.run、Hooks、MCPGateway、pool.close_pools | tests/mcp；test_backend_bounds；原始 MCP marker | 实测 stdio MCP 与异步取消/连接归还 | 不含语音、算法后训练；无法代替个人 Python 能力评估 |
| [J05 小红书 AI应用 2027 校招](https://job.xiaohongshu.com/campus/position/22021)：Tool/上下文/评测/Badcase/交付 | ResponsesPlanner、repo_map、evaluate、badcases | MockTransport 契约与 72 未运行槽位清单 | 实现真实模型接口及可复核证据出口 | 尚无真实模型修复示例；无生产 RAG/真实用户 |
| [J06 小红书 AI审核 2027 校招](https://job.xiaohongshu.com/campus/position/22072)：SQL/并发/可用性/软件工程 | Store事务、AsyncConnectionPool、BodyLimit、health | sql-plans.json（1000回滚合成行）；migration-probe.json；CLI HTTP | 记录真实 SQL 计划与有界连接/请求行为 | 无吞吐改善结论；270负载结果与拒绝分母见本轮独立报告；生产容量未知 |
| [J07 百度 Agent算法实习](https://talent.baidu.com/jobs/detail/INTERN/70a8d8a6-5aff-47ad-88c8-24aa47ab1812)：可复现任务/归因/Harness | CaseManifest、Verdict、suite.json、summarize | 12合成题、4dev/8holdout；反例测试 | 建立固定资产与可重算原始数据 | 偏研究要求不覆盖；非公开benchmark；开发者知道题目构造，非盲评 |
| [J08 小红书 AI Coding 2027 校招](https://job.xiaohongshu.com/campus/position/22079)：代码上下文/执行验证修复/质量 | repo_map、workspace.apply_patch、repair persistence | tests/repair/test_loop；test_repair_storage；repair-demo真实容器Artifact、repair-recovery/results.jsonl | 实现 AST/最多三候选/持久候选与证据联结 | 不是完整动态调用图；不是大型代码库理解；真实模型候选未授权；人工/Fake候选已真实执行 |

个人算法、OS、网络、SQL解释、模型基础、框架认知、独立修改和口头表达均 **NOT_ASSESSED**。参考工程完成情况不计入正式学习工程进度。独立练习见 interview_pack.md，最终验收由外部复核方完成。
