# 框架抽象对应与取舍

2026-10-25 阅读官方资料，未安装 LangGraph/PydanticAI，requirements.lock 未加入它们。因此没有本项目使用版本或实际使用经验；以下只是概念对应，不是兼容承诺。官方文档为读取日滚动版本，不能冒充某个已测试发行版。

LangGraph 官方区分 checkpointer 保存 thread 内状态和 store 保存跨 thread 数据。本项目 Store.checkpoint/Session 在同一 Task 跨 Attempt 恢复的职责接近前者，但不是同一个 API；本项目并未实现长期跨 Task 记忆。源码 `Store` 名称也不等同 LangGraph 的长期记忆 store。来源：[官方 Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

PydanticAI 的 Durable Execution 文档介绍与外部持久执行引擎的集成。本项目已有 PostgreSQL 事务、Lease/fence、Ledger 和 Worker，故保持现有六层，不另接第二个调度系统。候选提案协议与结构化 DTO 只是本项目的接口分工，不能据此宣称已经用过 PydanticAI。来源：[官方 Durable Execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)。

| 本项目责任 | 可比较的抽象 | 必须自己验证的边界 |
|---|---|---|
| Kernel / PreparedAction | 节点执行、工具动作 | 稳定逻辑Call ID、派发前审计、幂等/UNKNOWN |
| Store.checkpoint / Session | 持久状态快照 | Step/Artifact/Ledger同事务，最后完成步语义 |
| Worker / Attempt | 运行尝试、恢复 | 心跳/租约/fence；模型、远端副作用不是exactly-once |
| PatchPlanner / StrictDTO | 依赖接口与结构化输出 | 类型校验不等于测试通过，不授予shell |
| Independent Verdict | 任务质量门 | 当前候选、真实测试、完整报告、无旧PASS |

不据概念映射填入“熟练使用框架”。本人是否理解并能独立修改仍 NOT_ASSESSED。
