# Agent Runtime Core Reference

一个中文教学参考实例，用虚构采购资料说明 Durable Task、步骤恢复、工具权限与不确定副作用。它不是生产平台，不计入 `agent-runtime-core` 或 month01–month04 学习进度，也不代表正式 W8 验收通过。

**2026-10-26：真实 Docker 已可用；P0 A/C/F、36次独立Harness、25次容器生命周期、三类修复恢复及活容器SIGTERM验证通过。179测试零跳过通过，90+50故障探针与270次负载已完成；最终数字见 [TASK_STATUS.json](TASK_STATUS.json) 和对应 delivery 报告。模型明确禁用，真实模型修复和独立外部验收仍未进行，项目总体保持 PARTIAL。**

业务规则：超过 10000 要审批附件，所有资料要发票。DOC-001 为 12000、有发票无审批；DOC-002 为 8000、有发票；DOC-003 缺发票。资料和规则均有 ID、版本、虚构来源。检查程序依据金额、附件和规则字段计算结果；不是按 ID 返回答案。

实际组件包括 FastAPI、PostgreSQL 17.11、Alembic、psycopg 3 异步事务、官方 MCP 2.2.0 stdio、独立 Worker 与 Prometheus。`DeterministicPlanner` 是确定性规划器，不具有模型推理能力；模拟业务服务是独立进程测试依赖，在 `arc_business` schema 自己提交效果。Docker 后端已在固定镜像上完成真实容器验证。没有 Redis、Outbox、第二套 Agent 框架或前端。

## 环境与安装

操作路径必须是本参考工作区。初始参考目录非 Git 仓库；本轮依据 CODEX_MASTER_PLAN.md 授权建立了本地 Git 基线与小步提交，未推送。没有读取、复制、import 或修改正式学习工程源码。

本轮默认系统 Python 为 3.13.9，已有 agent-dev 环境实际为 3.11.15。最终在本目录建立独立 `.python`（3.11.17）、`.venv` 和 `.postgres`（17.11）。未修改系统 Python。精确依赖见 `requirements.lock`、`deploy/python-conda.lock`、`deploy/postgresql-conda.lock`。以下安装方式已实际执行；已有安装不必重复创建：

```bash
cd /home/dts/agent-runtime-core-reference
/home/dts/anaconda3/bin/conda create --prefix /home/dts/agent-runtime-core-reference/.python --offline python=3.11.17 -y
/home/dts/anaconda3/bin/conda create --prefix /home/dts/agent-runtime-core-reference/.postgres --override-channels -c conda-forge postgresql=17 -y
.python/bin/python -m venv --upgrade .venv
env -u PYTHONPATH .venv/bin/python -m pip install -r requirements.lock
.venv/bin/python --version
```

本轮修复过旧 venv 指向 3.11.15 的链接；最终链接指向本项目 `.python/bin/python3.11`。从空目录安装时直接执行 `.python/bin/python -m venv .venv` 即可；这是标准重建步骤，本轮升级过程和异常记录见 environment.json。清除 `PYTHONPATH` 仅影响该命令，避免宿主 ROS 插件被 pytest 自动发现。

配置由 `config/settings.py` 经环境变量加载，示例见 `.env.example`。默认身份均明确为本地演示凭据，没有真实模型 Key。数据库默认使用仅绑定回环的专用本地演示实例与 trust 认证；不能开放给远程用户或当生产配置使用。

已实际执行的首次数据库初始化：

```bash
.postgres/bin/initdb -D /home/dts/agent-runtime-core-reference/.pgdata -U arc_ref --auth=trust --encoding=UTF8 --no-locale
```

这个命令只能用于新的 `.pgdata`，不要覆盖已有库。数据库文件应保留以查询证据。`deploy/compose.yaml` 是另一个独立 PostgreSQL 启动配置，固定镜像 digest；本轮采用已有本地 PostgreSQL，所以该备选配置**未运行**，且不能与本地 PostgreSQL 同时占用 55439。

## 启动、观察与停止

以下 Make 命令与本轮实际运行入口一致：

```bash
make db-start
make migrate
make start
make check
make demo
make stop
make db-stop
```

`make start` 启动 API `127.0.0.1:18081`、独立模拟业务服务 `127.0.0.1:18082`，把名称/PID/端口写入 `reports/services.json`。它不启动持续消费队列的 Worker；`make demo` 和 process 测试按任务启动独立 Worker 并清理。交互学习时可以在另一终端运行 `make worker`（本轮使用的也是此模块的 `--once --task-id` 形式），用 Ctrl-C 结束该前台 Worker。

`make start` 是演示入口，会为模拟服务设置 `ARC_TEST_MODE=true`；故障屏障只能通过可信进程环境和测试数据库控制，普通 API 请求没有故障开关。每个 Worker 的屏障环境由场景脚本单独设置。

`make images` 已实际尝试，本轮返回 BLOCKED（Docker socket permission denied）。它仅拉取固定检查镜像，使用空 Docker CLI 配置避免读取已有 registry 凭据；需要已授权的 Docker 接口后才能补跑真实容器场景。

API `/docs` 提供交互接口。用 `.env.example` 的本地演示 Bearer 凭据授权，POST `/tasks` 传 `Idempotency-Key`，请求示例：

```json
{"task_type":"document_review","document_id":"DOC-001","session_id":"my-first-review","budget_ms":120000,"publish_mode":"none"}
```

提交事务成功后返回 202。GET `/tasks/{task_id}` 看一致快照，`/diagnostics` 看真实 Task/Attempt/Step/Action/Ledger/Audit/Artifact 元数据，`/events` 看 SSE，`/artifacts/{artifact_id}` 下载 BYTEA 内容。`/metrics` 从数据库汇总 Worker 持久结果，标签没有 task_id。取消用 POST `/tasks/{task_id}/cancel`。对账用独立 operator 身份和 `/reconcile`，必须给版本 CAS、工具调用 ID、结论和证据摘要。

`make stop` 只停止记录中且工作目录/命令归属本项目的 API 与模拟服务；`make db-stop` 只停止本项目 `.pgdata`。不要执行全局 Docker 清理。本轮已真实运行工具容器并验证归属清理；最终剩余资源以resource-cleanup.json为准。跨组/proc权限不足记为UNKNOWN，不宣布停止。

## 如何理解结果

- Runtime `SUCCEEDED`：流程完成，结果已提交。其业务 `review_decision=NEEDS_DOCUMENTS` 仍然是一次成功审核。
- Runtime `NEEDS_REVIEW` + Ledger `UNKNOWN`：外部效果可能发生、结果无法确认；不能自动重发不安全工具。
- `DOCKER_UNAVAILABLE`：基础设施不可用时的明确错误，任务失败，不能伪造审核结论或归为业务材料缺失。

幂等外部请求使用已保存的随机 `tool_call_id`。模拟服务分别保存请求次数和效果次数；重发幂等请求允许 2 次请求但只能产生 1 次效果。不安全模式崩溃恢复只有 1 次请求，进入人工对账。可查询模式只接受肯定“已生效”的权威查询；404 不等于未执行。幂等 HTTP 网络错误目前保守转 NEEDS_REVIEW，没有实现自动重试调度；Worker 崩溃恢复的原键重发已单独验证。

## 证据与源码入口

先读 [core/kernel.py](core/kernel.py) 的 `Kernel.run`，再配合 [源码阅读顺序](docs/source_reading_order.md) 与 [任务讲解](docs/learning_walkthrough.md)。[架构](docs/architecture.md)、[故障讲解](docs/failure_walkthrough.md)、[覆盖边界](docs/design_coverage.md)、[面试追问](docs/interview_questions.md) 均引用实际文件和符号。

`reports/20261022-reference/` 保存真实环境、命令及退出码/时长、JUnit、各场景 task_id 与不变量、首次失败和修复后的结果。报告文件是导出证据，PostgreSQL 才是恢复事实源。审计不可用时拒绝新的副作用，但数据库自身不可用可能无法持久化拒绝日志；追加审计不抵御数据库超级用户修改。本轮重复故障、真实容器与独立270次负载的结果以新run和delivery为准；旧报告不覆盖。

MCP API 按官方 [2.2.0 包元数据](https://pypi.org/pypi/mcp/2.2.0/json)、[Client](https://py.sdk.modelcontextprotocol.io/client/) 与 [stdio transport](https://py.sdk.modelcontextprotocol.io/client/transports/) 核对，再用实际安装源码和协议测试确认。采用 `Client`、`StdioServerParameters`、`MCPServer`；2026-07-28 路径不手写 v1 initialize 握手。

## 受控代码修复增强

已加入 code_repair：严格 Manifest、独立 Verdict、基准+patch 重建、最多三候选持久循环、Python AST 上下文、结构化模型 Adapter 与持久预算。参阅 [阅读路径](docs/repair_reading_path.md)、[威胁模型](docs/threat_model.md)、[岗位证据](docs/job_requirements_matrix.md)。实际Docker已验证；Fake业务结果按预期UNRESOLVED，真实模型未授权，没有模型DELIVERED证据；不代表完整 Coding Agent 或用户已独立掌握。唯一恢复入口 [resume.md](resume.md)。

2026-10-26后续增量：实验取消/强杀记录与归档校验已加固，112项单元/数据契约通过。原完整包保持不变，当前增量边界和后续工作见[恢复入口](resume.md)与TASK_STATUS.json的latest_incremental_validation。

2026-10-26 后续验证（`20261026T032837Z-steady-load`）：当前完整pytest 212通过、零跳过；配额内并发1/2/3的270次独立提交全部成功；810事件与真实PG及Artifact一致，观察器SIGKILL后同幂等键对账恢复通过。详见[恢复入口](resume.md)；原1/5/10过载数据单独保留，真实模型仍未运行。

三项研究已启动：[研究协议与结果](docs/three_track_research.md)。当前完整218测试通过；36个外部副作用对照样本完成，区分稳定幂等键与UNKNOWN恢复策略的作用。真实模型仅完成离线单dev计划、尚未授权；[个人独立练习](exercises/observation_diagnosis/README.md)待本人实现。
