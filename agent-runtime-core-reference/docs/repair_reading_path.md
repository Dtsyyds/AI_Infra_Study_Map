# 从请求到补丁的阅读路径

本轮加入 code_repair，但未获得真实 Docker/模型端到端成功证据。原 document_review 仍用确定性 Planner。本文件描述代码，不把已实现等同已验收。

1. `core/contracts/task.py` / `api/main.py` / `examples/cli.py`：真实 `/tasks` 接口；CLI 经鉴权 HTTP 调用提交、状态、取消、等待、诊断、Artifact。
2. `core/contracts/repair.py` / `runtime/repair/manifest.py`：可信 CaseManifest 与固定测试资产。Store.submit 将 Manifest 和基准文件写入 Task.context，202 在事务提交后返回。
3. `runtime/orchestrator/worker.py` / `core/kernel.py` / `core/nodes.py::RepairPlanner`：认领+Attempt 同事务，先恢复持久 Action，再规划。checkpoint 是最后完成步；下一步 checkpoint+1。
4. `runtime/storage/store.py` / `runtime/repair/persistence.py` / `deploy/alembic/versions/0002_repair.py`：Action+PREPARED、派发前 Ledger+Audit、Step+Artifact+Session+候选/验证同 checkpoint 事务。新 Attempt 不增加 candidate_no。
5. `runtime/tools/repair_tools.py`：先 baseline；只有真实预期 FAIL 后 reserve/generate；最多 3 候选。Fake 明确标记且不假装真实修复，real 必须有配置与持久预算。
6. `runtime/context/repo_map.py` / `repair_context.py` / `runtime/planning/adapters.py`：Python AST 与有限片段，动态调用 UNRESOLVED；结构化模型响应不授予 shell 权限。无自动 Fake 回退。
7. `workspace.py` / `harness.py` / `deploy/repair_runner.py` / `verdict.py`：从基准+patch重建，新容器验证，当前版本独立判定。不能靠旧容器磁盘恢复，验证缓存关闭。
8. `validate_delivery` / `Store.finish`：当前候选有效 PASS 才 DELIVERED；UNKNOWN、环境错误、取消与业务未解决分开。诊断联结候选、物理验证、Step、ToolCall 和 BYTEA Artifact。

冻结语义仍为 PREPARED/DISPATCHED/COMMITTED/UNKNOWN 及源码已有 reconciliation 状态；不改成早期设计枚举。Session 是同 Task 跨 Attempt 恢复，不是跨 Task 长期对话。TelemetryHook 失败不影响持久业务结果；恢复只信 PostgreSQL。Prometheus 不放 Task/Candidate 等高基数标签。

六层位置：L1 Kernel 协调；L2 Store 事务；L3 ToolRuntime/ledger 安全执行；L4 Context 重建；L5 Worker 租约与生命周期；L6 API/CLI/监控。新增 repair 子模块是现有层内职责，不是第七层平台。
