# 原小任务到本轮源码的映射

原 enhancement_plan.md 是历史方案，不覆盖或把其估算当实际工时。本轮契约为 CODEX_MASTER_PLAN.md，开发运行时间见 commands.jsonl；没有逐片审批，也未把新增工作塞入 W7。

| 历史任务 | 实际落点 | 状态与差异 |
|---|---|---|
| R00–R05 | DockerBackend.inspect_state/_remove_owned/cleanup_orphans、tests/docker/test_inspect_errors.py、examples/p0_revalidate.py | 安全修复和证据入口已写；真实 A/C/F BLOCKED |
| H01 | core/contracts/repair.py、runtime/repair/manifest.py、tests/repair/test_contracts_verdict.py | 严格 DTO、基准/测试/runner hash 与策略校验已测 |
| H02 | runtime/repair/verdict.py、同一反例测试文件 | 按新契约使用 PASS/FAIL/ERROR/NOT_RUN/INCOMPLETE，未沿用旧 INVALID 设计 |
| H03 | runtime/repair/workspace.py、test_contracts_verdict.py | 精确文本替换、范围/大小限制、基准重建已测；非通用 diff |
| H04 | examples/build_repair_cases.py、examples/repair_cases/case-01..12 | 4dev/8holdout原创资产；沿用已锁定镜像与stdlib，未新建Dockerfile/依赖锁 |
| H05–H06 | runtime/repair/harness.py、deploy/repair_runner.py、tests/docker/test_repair_harness.py | 可信runner/独立Harness/12例人工patch测试已实现；实际运行BLOCKED |
| M01–M02 | runtime/context/repo_map.py、repair_context.py、tests/repair/test_context_model.py | AST+固定允许小文件；不承诺动态图/大型仓库检索 |
| P01–P04 | core/contracts/planning.py、runtime/planning/adapters.py、config/settings.py、model_calls/model_budgets | 无SDK升级；httpx Responses Adapter与Fake/录制契约；真实兼容性与调用BLOCKED |
| D系列 | deploy/alembic/versions/0002_repair.py、runtime/repair/persistence.py、Store/Kernel/Worker/RepairPlanner、tests/integration/test_repair_storage.py | 候选/验证/预算持久化及原事务集成已测；repair独立进程只验证patch提交后恢复，运行中/结果后退出仍待Docker |
| E系列 | examples/evaluate.py、load.py、reliability.py、additional_faults.py、summarize.py | 两类实验分离；72/270未运行；故障原始记录140条有严格范围标签 |
| G5/G7新增 | API body/health、pool.py、CLI、SQL/migration探针、CI、docs与package_delivery.py | 本地实现与证据出口；远端CI/生产容量/完整终态延迟分析未验证 |

实施顺序保留 H01→独立 Verdict→候选约束→runner/持久恢复→模型接口。先有验证器才能区分真实测试失败、测试没运行、报告被篡改与旧 PASS；模型接入不掌握判分权，循环只能消费已提交、当前版本的证据。

下一会话的最小动作是先在已授权 Docker 会话复跑 P0 与12例人工patch矩阵；若发现 runner 兼容性问题，先修它并保留失败。之后补测试运行中/结果提交后故障与容器矩阵，最后经明确模型预算授权在dev集做一次真实smoke。无权环境不尝试宿主候选替代。

2026-10-25收尾：增加examples/experiment_driver.py与experiment_metrics.py（原始提交、Artifact及分母/阶段时间/用量）、backend_probe.py（PG锁与SIGTERM）、harness_acceptance.py（36次独立资格）、repair_recovery.py（三恢复点）、sandbox_matrix.py（5类×5）、verify_delivery.py（归档自校验）。未知归属/错误候选/假调用计数另有回归测试。上述Docker入口只预检BLOCKED，不能把implemented写成已验证。需要用户的最小动作见remaining_user_actions.md。
