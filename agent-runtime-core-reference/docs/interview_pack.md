# 45 分钟面试提纲与独立练习

材料定位：可复核的教学参考工程，Codex 协助实现。个人理解、算法/OS/网络/数据库/模型基础、口头表达均 NOT_ASSESSED。不要把这份代码描述成实习或生产经历。

| 时间 | 主题与应能回答的问题 | 实际阅读入口 |
|---|---|---|
| 0–5 分 | 为什么有 Task/Attempt/ToolCall/Candidate/Verification 五种身份？本轮哪些证据不足？ | FINAL_REPORT、repair_reading_path |
| 5–15 分 | 202、claim、prepare、dispatch、checkpoint 分别在什么事务边界？取消与旧 Worker 怎么竞争？ | Store、persistence、0002_repair |
| 15–25 分 | 为什么 exit 0 不等于通过？如何拒绝旧 PASS/skip/空测试/另一 Task 的报告？ | verdict、harness、负例测试 |
| 25–32 分 | UNKNOWN 如何恢复？为什么 fence 不能撤销远端 HTTP？幂等与对账有什么不同？ | process_scenarios、Ledger |
| 32–38 分 | AST 何时不可靠？持久模型预算如何处理网络未知计费？ | repo_map、adapters、model_calls |
| 38–45 分 | 如何独立复跑并质疑本次证据？哪些门 BLOCKED？ | REPRODUCE、原始 JSONL、JUnit |

待本人在另一获准练习分支完成并解释（未完成，不替用户打勾）：

1. 为一类新的报告不一致先写失败测试，再实现最小拒绝规则，证明正常报告不受影响。
2. 给固定合成 dev 案例新增一条回归测试；更新 suite hash 与 Manifest；解释为什么旧 PASS 必须失效。不得用 holdout 调 Prompt。
3. 从 SQL 原始计划解释 claim 索引与锁边界，设计同条件对照测量，没测得提升就不写百分比。
4. 在已授权 Docker 环境独立复现一次 Worker 强杀与清理 UNKNOWN，导出 Task/Attempt/ToolCall/容器关联。
5. 不看作者说明，口述取消后 DELIVERED 被哪个事务拒绝，并实际修改后验证。

这些练习的结果由最终复核方与本人确认。本代理不代签“掌握”。
