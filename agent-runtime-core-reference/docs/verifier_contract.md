# Independent Test Harness / Verdict 契约

入口：`runtime.repair.harness.verify(backend, manifest, files, identity, ...)`，不需要 Planner。`files` 是基准加受控 Patch 得到的文本树，不在宿主 import 或执行。`runtime.repair.verdict.judge` 只处理数据。

CaseManifest 严格拒绝额外字段/非法类型；基准使用真实 snapshot_hash（本案例未伪造 Git revision）。固定 repo/test/image/runner hash、预期 ID、基线 fail/pass 划分、允许路径及资源预算。源码资产由 load_case 验证，无符号链接。v1 patch 是批准现有文件的唯一文本替换，非通用 git diff，最多 3 文件/200 变更行/32 KiB。

| Verdict | 含义 |
|---|---|
| PASS | 当前身份、版本、测试集、完整报告、退出码、资源与清理全部匹配，必要测试均通过 |
| FAIL | 完整可信运行中实际断言失败；可进入下一候选 |
| ERROR | 身份/策略/基础设施/运行记录冲突或资源异常 |
| NOT_RUN | 未证实可信 runner 启动，例如 Docker 不可达 |
| INCOMPLETE | 报告或必要测试结果缺失/不完整 |

精确 reason_code 以 `runtime/repair/verdict.py` 为准。空测试、全 skip、xfail/xpass、缺失/重复测试、损坏/截断/重复 JSON 键、旧候选/另一 Task/不同 nonce PASS、退出码矛盾、超时残留 PASS、保护文件变更均不能产生 PASS。预期失败必须在基线真正观察到，不能只检查 manifest 自述。

trusted runner 使用镜像已有 stdlib unittest（`python -I -B -c`），未安装 pytest 或升级镜像。开始标记 `ARC_REPAIR_STARTED`，报告标记 `ARC_REPAIR_REPORT`，报告输出受限。容器证据包含运行/退出/检查/清理；无法判断状态为 UNKNOWN。报告仍可能被同解释器恶意代码影响，见 threat_model.md。

数据库 `repair_verifications` 绑定 Task/Attempt/ToolCall/verification_run_id；提交时从当前可信行重算 Verdict，不能接受调用方传入的 PASS。`validate_delivery` 仅允许最新已提交候选匹配有效 PASS 后 DELIVERED。Runtime SUCCEEDED 仍可对应 UNRESOLVED，三类结果不混用。

数据反例与真实 PG 事务测试已运行；12 个基线与人工正确 patch 的 Docker 测试均 BLOCKED。测试用人工 gold 仅位于可信 `tests/repair/private_reference_patches.json`，模型上下文没有该文件。开发者知道合成题构造过程，所以不是独立盲评。

2026-10-25：独立套件资格入口为 `examples.harness_acceptance`，逐例验证基线/正确/错误补丁并冻结结果及当前Manifest hash；真实模型批量入口拒绝缺失/陈旧/损坏资格证据。`test_pause`仅由可信测试进程选择，用于在测试开始后制造恢复故障，模型和API无法设置。gold仍只在可信校验侧读取。当前36次实际运行仍BLOCKED。
