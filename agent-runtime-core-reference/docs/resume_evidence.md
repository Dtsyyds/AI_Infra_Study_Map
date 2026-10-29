# 简历证据草稿（待本人独立复核）

Agent Runtime Core — Reliable Coding Task Execution；个人学习参考项目，Codex辅助实现，不冒充公司实习、生产平台或公开benchmark。

- 六层Python/PostgreSQL Runtime：持久受理、事务checkpoint、lease/fence、工具策略、独立Verdict、有界候选、AST上下文。真实Docker的P0正常/恢复/超时链路通过。
- 本轮20261026T021326Z-docker-free-validation：179测试通过、零跳过；36次固定案例独立Harness；25次容器生命周期；3类修复恢复+活容器SIGTERM；90+50限定范围故障记录。每个数字均由原始数据与命令追溯。
- 270次确定性负载提交：SUCCEEDED 108、REJECTED 162；拒绝为QUOTA_EXCEEDED 162，不混入成功延迟、不宣称270次成功或生产性能改善。
- 固定案例人工参考修复可通过可信容器验证；Fake控制流按预期UNRESOLVED。真实模型调用0，因此没有模型修复率、token成本或真实模型成功修复可宣传。

source_manifest、tests、benchmarks及artifacts保存版本/字节/hash。个人独立实现、算法、OS、网络、DB、模型、表达能力仍NOT_ASSESSED；外部验收NOT_PERFORMED。

补充运行`20261026T032837Z-steady-load`：当前完整212测试零跳过；独立steady并发1/2/3，各90例，共270/270成功。810事件与270个PG任务/幂等键/Artifact一一核对，真实模型调用0。观察器强杀后按原幂等键找回同一任务，未重复创建。该阶段约8.4分钟闭环实验含限流等待与每任务Worker启动，不作为生产吞吐量或相对旧过载实验的性能提升。原108成功/162拒绝与新270成功属于不同实验，不合并成功率。

研究补充`20261026T070223Z-three-track-research`：三臂（重启新key/仅持久key/Runtime）×两种服务，共6无故障控制与30固定屏障崩溃样本。稳定key在幂等服务上足够去重，但unsafe服务仍出现额外效果；Runtime的unsafe5/5进入NEEDS_REVIEW，显式测试操作员确认后完成，无额外派发。范围为局部跨HTTP事务恢复消融，不宣传通用exactly-once或生产故障率；当前完整回归218通过。单dev模型计划仅离线准备；本人独立练习尚未开始。
