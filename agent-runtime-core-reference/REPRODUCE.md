# 独立复现入口

项目总体仍为 PARTIAL（真实模型未授权、外部验收未进行）；2026-10-26 本轮免费验证及修复证据以 TASK_STATUS.json 和对应 FINAL_REPORT.md 为准。包中 `source/` 为源码快照，`reports/<run_id>/` 为原始证据；请先核对 manifest/hash。当前工作区直接在根目录执行。解压包请先进入 source/，复跑产生新的 reports，不覆盖包中原始证据。

## 环境

使用 Python 3.11（本轮3.11.17）、PostgreSQL17（本轮精确版本见 environment.json），`requirements.lock`、`deploy/*-conda.lock` 是既有锁文件。依赖下载需在获准联网环境执行；没有自动升级 SDK。创建独立 venv 并安装：

```bash
python3.11 -m venv .venv
env -u PYTHONPATH .venv/bin/python -m pip install -r requirements.lock
```

数据库 URL 通过 ARC_DATABASE_URL 注入，禁止把真实密码写到命令记录。参考工作区已有本地 `.postgres/.pgdata`：`make db-start`；新环境只在新的数据目录 initdb，创建专用 arc_reference 库并绑定回环，或使用组织已授权测试数据库。绝不重置已有库。`deploy/compose.yaml` 为备选，当前未实际运行，不可与本地 PG 占同端口。

选择新的 UTC run_id，更新 TASK_STATUS.json 的 run_id（只变本次导出目录）；先保留历史报告。以下命令用 examples.evidence 包裹可保存 stdout/stderr、退出码、源码 hash；它需要本地 Git 仓库。源码归档无 .git 时，先以包内 source_manifest 追溯来源，使用已配置身份建立新的本地快照，不能假造旧 commit。

```bash
env -u PYTHONPATH .venv/bin/alembic upgrade head
make start
env -u PYTHONPATH .venv/bin/python -m examples.environment
env -u PYTHONPATH .venv/bin/ruff check core runtime api config tests examples deploy
env -u PYTHONPATH .venv/bin/mypy
env -u PYTHONPATH .venv/bin/python -m pytest tests/unit tests/repair -q -rs
env -u PYTHONPATH .venv/bin/python -m pytest -q -rs --junitxml=reports/new-test-results.xml
```

全量 pytest 包含真实 PG、独立本地业务服务与 MCP。Docker marker 缺环境会 skip 并说明 BLOCKED，不能据进程退出0宣布全链路成功。`tests/docker/test_script_unit.py` 只测试固定的旧审核脚本；从不在宿主执行 code_repair 案例。不要直接运行 `examples/repair_cases/**` 或其测试。

## Docker 授权环境下

先由环境所有者提供已获准访问现有 daemon 的会话；不改 socket 权限/用户组，不 sudo。检查 `docker context show`、`docker version`，通过 `make images RUN=<新run>` 仅拉取配置的固定镜像（不读取已有 registry 身份）。随后：

```bash
env -u PYTHONPATH .venv/bin/python -m examples.p0_revalidate
env -u PYTHONPATH .venv/bin/python -m pytest tests/docker -q -rs
env -u PYTHONPATH .venv/bin/python -m examples.repair_demo
```

P0 应有 DOC-001/002 最终报告 BYTEA/hash；C 要两个独立 Worker 与实际 MCP1→0；F 必须先见容器启动，再超时与终止/归属清理。12例原始缺陷与人工参考 patch 必须在容器真正验证。G0/G1/G2须使用本轮真实执行证据判断，不能仅凭入口或历史报告。

## 故障与工程入口

```bash
env -u PYTHONPATH .venv/bin/python -m examples.reliability
env -u PYTHONPATH .venv/bin/python -m examples.additional_faults
env -u PYTHONPATH .venv/bin/python -m examples.repair_process_probe
env -u PYTHONPATH .venv/bin/python -m examples.sql_probe
env -u PYTHONPATH .venv/bin/python -m examples.migration_probe
```

前两者拒绝覆盖 raw.jsonl，必须用新 run_id；其他探针也须新 run_id 避免覆盖。repair_process_probe 明确注入合成基线，仅证明持久补丁恢复，不证明修复。migration_probe 只创建并删除自己的新空库，需要现有角色已有 CREATE DATABASE 权限，否则 BLOCKED。sql_probe 的1000合成行在事务内回滚，不能当性能提升。

CLI：`.venv/bin/python -m examples.cli submit --case case-01`，继而 `status <id>` / `cancel <id>` / `wait <id>` / `diagnostics <id>` / `artifact <task> <artifact> --output <path>`，均走本地鉴权 HTTP。不要把 submit202 当任务成功。`cli_probe` 需同run先运行 repair_demo，因为会复核其已导出的 Artifact。

## 真实模型与实验（需要独立明确授权）

先核对 docs/model_backend_compatibility.md、当前官方API与选定模型的结构化输出/采样参数支持。审批后只向可信 API/Worker 进程注入 ARC_MODEL_ENABLED、ARC_MODEL_NAME、ARC_MODEL_SECRET_ENV、ARC_MODEL_BUDGET_ID、ARC_MODEL_REQUEST_LIMIT、ARC_MODEL_TOKEN_LIMIT、ARC_MODEL_MAX_OUTPUT_TOKENS、ARC_MODEL_TASK_REQUEST_LIMIT 和指定秘密变量。不要把密钥写在命令行/日志/容器。默认预算0；本轮未读取真实密钥，也没有真实调用。预算不足保持BLOCKED，不回退Fake。

先在 dev 集单任务使用 `examples.cli submit --case case-01 --planner real --strategy loop`，为返回 Task 启动 `python -m runtime.orchestrator.worker --once --task-id <id>`。只有当前验证 PASS 与最终 DELIVERED、真实 provider/model/request/usage 证据齐全后，才算一个真实修复例。人工审阅全局预算后才运行：

```bash
env -u PYTHONPATH .venv/bin/python -m examples.evaluate --execute
env -u PYTHONPATH .venv/bin/python -m examples.load --execute
env -u PYTHONPATH .venv/bin/python -m examples.summarize reports/<run>/reliability/raw.jsonl reports/<run>/reliability/summary.json
```

72模型任务与270确定性提交单独原始数据，不混表。确定性负载不调用模型，可在不授权模型的情况下独立执行；模型实验仍须另外授权。每轮负载读取数据库限流窗口并等待其自然到期，等待时间保存在load_windows.jsonl，不计入任务延迟、不重置或放宽配额。

结束时 `make stop`、`make db-stop`（仅限本会话启动的本项目实例）。保留数据，不执行 prune/未知卷删除。新会话从 resume.md 开始，不假定后台继续。

## 2026-10-25 新增复现入口

已授权Docker环境中，先运行 `python -m examples.harness_acceptance`：独立校验12例的基线、人工正确与错误补丁，共36次真实验证，导出完整报告与当前Manifest hash。`evaluate --execute` 会拒绝缺失或陈旧的Harness qualification，不能先烧模型预算再发现套件不可信。

随后运行 `python -m examples.repair_recovery`（补丁提交后、验证提交后、测试运行中三个真实独立进程恢复点）和 `python -m examples.sandbox_matrix`（正常/超时/内存/取消/强杀恢复各5次）。2026-10-26已执行真实分支；修复恢复入口现在额外包含活容器SIGTERM优雅退出（共4个恢复点），原始结果见本轮报告。失败或BLOCKED槽位不能算执行成功。trusted test_pause由可信Worker测试配置设置，普通API与模型没有该参数。

无需Docker即可复跑 `python -m examples.backend_probe`，它观察真实PG锁等待/连接池和在工具派发前屏障上的Worker SIGTERM退出；不是活容器取消证据。

实验结束后可独立重算：

```bash
env -u PYTHONPATH .venv/bin/python -m examples.experiment_metrics model reports/<run>/benchmarks/model_raw.jsonl reports/<run>/benchmarks/model_summary-recomputed.json
env -u PYTHONPATH .venv/bin/python -m examples.experiment_metrics load reports/<run>/benchmarks/load_raw.jsonl reports/<run>/benchmarks/load_summary-recomputed.json
env -u PYTHONPATH .venv/bin/python -m examples.verify_delivery delivery/<run>
```

未运行实验没有raw文件，不创建假的空执行记录。预算预留数量不同于确认收到模型响应的调用数量；未知token用量保持缺测。排队超时且未认领时execution_seconds为null，拒绝请求不混入成功耗时。归档校验PASS仅表示hash/计数/引用一致，不是项目验收通过。

## 2026-10-26 组会话与免费验证

本机 `getent group docker` 已包含 dts，但原Codex进程继承的组列表没有983；经本会话批准使用 `sg docker -c '<项目命令>'` 激活已存在的组成员权限后，Docker Client/Server均可用。没有修改组成员、socket权限或使用sudo。这不是跳过权限检查；仅适用于环境所有者已经授予Docker组成员资格的情况。

API/业务服务、测试和Worker应从同一有效组会话启动和停止。跨组读取/proc可能被拒绝，服务管理脚本现在报告UNKNOWN并保留记录；须回到启动它的组会话停止，不能把UNKNOWN写成已停止。PostgreSQL仍由本次启动它的原组会话停止。

所有免费验证命令显式使用：

```bash
export ARC_MODEL_ENABLED=false ARC_MODEL_REQUEST_LIMIT=0 ARC_MODEL_TOKEN_LIMIT=0
unset PYTHONPATH
```

在有效Docker组会话执行既有入口。`examples.load --execute` 是免费确定性负载；`examples.evaluate` 不加 `--execute` 只记录模型未授权。保留旧报告，使用新run_id。不得在宿主执行案例或gold测试。

新增反例：`tests/unit/test_worker_logging.py` 验证依赖库设置ERROR日志后Worker仍输出清理证据；`tests/unit/test_services.py` 验证服务进程权限未知不会被宣布停止或覆盖PID记录。真实恢复探针使用现有demo-reviewer策略身份，未放宽工具权限。

本轮负载首轮因Artifact元数据缺少sha256而中断，旧raw保留；修复后的正式负载使用 `python -m examples.load --execute --attempt 2`，输出位于 `benchmarks/load-attempt-2/`。统计命令的输入/输出也使用该目录；重复实验须递增attempt或换run_id，不覆盖旧文件。默认attempt=1保持原入口行为。

## 证据工具增量复验

`python -m pytest tests/unit tests/repair -q` 验证提交取消/强杀、证据拒绝覆盖和归档一致性反例。本轮112项不替代旧完整179项回归。事件日志为 `load_raw_events.jsonl` 或 `model_raw_events.jsonl`，均在对应实验目录；只有INTENT/ACCEPTED而无完整结果的身份仍属未决，不能当成功或未受理。CancelledError原样传播，原始结果会记录OBSERVATION_ERROR。

旧run/attempt的配置、raw或预检记录存在时，入口会在写入前拒绝；应选择全新run或load attempt，不删除旧证据绕过保护。模型驱动不判断负载结果，负载数量以其独立raw/summary为准。新归档校验器会复核实际文件集合、JUnit testcase计数、真实日志存在性和被测源码快照；即使所有hash重新计算，内部不一致也不能PASS。

## 配额内负载与事件核对

在新run、模型关闭、真实PG/API/业务服务和Docker可用的环境执行：

```bash
env -u PYTHONPATH .venv/bin/python -m examples.load --execute --profile steady
env -u PYTHONPATH .venv/bin/python -m examples.verify_load reports/<run>/benchmarks
```

`steady`固定并发1/2/3，各3轮、每轮30个独立任务，共270次提交；默认`overload`仍为1/5/10。两种配置必须放在不同run或attempt中，不混合计算。每个客户端槽位等待任务终态观察及Artifact导出后再提交；每轮等现有限流窗口自然到期，不放宽3个活任务/每分钟30次的配额。每任务新建Worker的开销计入观测，结果不是常驻Worker的生产吞吐量。

`verify_load`只读取完整实验目录，核对计划槽位、提交/任务唯一性、INTENT→ACCEPTED→FINISHED顺序、请求与诊断身份、数据库时间推导的耗时、实际Artifact字节/hash、Worker日志和汇总重算。有未决事件、缺失结果或内部矛盾会失败；一致性PASS不代表所有任务成功，仍须检查outcomes。被强杀的观察器仅留下INTENT/ACCEPTED时，不得补造FINISHED；可用原请求和原幂等键显式对账，恢复结果存到独立报告。

## 三项研究入口

研究协议和结果解释见`docs/three_track_research.md`。在模型关闭、预算0的新run内，`python -m examples.model_smoke_plan --output reports/<run>/model-smoke-plan.json`只生成本地待授权计划；不会执行真实模型任务。

已有PG/API/独立业务服务启动后，`python -m examples.reliability_comparison --repeats 5`执行6个无故障控制加30个固定时机崩溃样本。模型必须关闭且预算0；输出目录存在即拒绝，不覆写旧失败。直接调用组只访问本地模拟业务；Runtime组使用已有局部L2/L3发布恢复探针，不执行Agent候选。原始结果在`reliability-comparison/`，每条raw引用真实独立进程日志。使用`summarize(rows, repeats)`可以离线重算；本run的`comparison_audit.py`在数据库仍运行时可独立核对业务事实。

独立练习见`exercises/observation_diagnosis/README.md`；fixtures为合成输入，MY_WORK待本人填写，不作为运行证据。研究命令不能替本人完成练习。结束时仍按同组会话停止本轮服务及PG并保留数据库。
