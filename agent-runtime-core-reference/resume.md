# 恢复入口

## 2026-10-29 产品化启动（当前入口）

用户已同意按“可靠代码修复工作台”M1–M6计划推进，新增前端/项目/审批范围见 [工作台计划](docs/workbench_plan.md)。历史基线保持，下文2026-10-26的完整测试及研究结论仅属于原run。

本轮完成：只读硬件检查、Docker client/server握手；新增 `examples.dev_preflight` 与 `make preflight`；建立环境建议和产品/学习路线。新增模块Ruff、mypy通过；普通预检返回CAUTION，strict模式按预期退出2。未重跑完整218测试，未启动项目服务或容器，未调用模型。

当前资源：8核16线程、14.99 GiB物理内存、约9.3 GiB可用内存；工作区分区空闲约6.6 GiB，使用98%。先恢复至少20 GiB空闲、推荐30–50 GiB，再做大量依赖/镜像安装。可以继续轻量源码、API契约和单元工作；未删除任何数据。Docker在已有授权组会话可达，普通宿主会话仍缺socket访问权限。

下一动作：本人先完成独立诊断练习的输出契约（MY_WORK仍空白），代理继续M2任务列表/候选查询/API权限契约。单任务/单容器是起步建议，尚未改变Runtime配额。模型后端和正预算未确认；不因产品化计划自动开始付费调用、远端推送或实际PR发布。

## 历史研究交付（2026-10-26）

最新run：20261026T070223Z-three-track-research。用户要求按真实模型闭环、可靠性对照、本人独立修改三项开始研究。本阶段已完成可运行的免费研究与准备；总体PARTIAL，真实模型0调用、独立外部验收NOT_PERFORMED、个人掌握NOT_ASSESSED。

当前完整pytest 218通过、零失败/零跳过；Ruff通过，mypy86文件通过。最终被测源码commit 7273b4c（实验执行bb00cf7，之后仅澄清两处描述）；最终文档提交及hash见新包source_manifest。所有本轮API/业务服务/PG已停止，18081/18082/55439关闭，项目容器0，.pgdata保留。

## 三项当前状态

1. **真实模型闭环：PREPARED_NOT_AUTHORIZED。** examples.model_smoke_plan生成单case-01 dev实验草案，冻结提示词/Schema/Manifest/镜像/runner。草案3请求、单次2048输出token、100000本地预留单位未获批准；实际仍禁用、预算0。模型/账号/参数兼容性待确认，不能把本地Schema检查与MockTransport算真实API成功。未来授权后先当前Harness qualification，再单dev smoke；72矩阵另行批准。不会读取或要求在聊天粘贴密钥。
2. **可靠性对照：36/36完成且独立核对。** 三种策略×两种服务：6无故障控制+30固定窗口SIGKILL样本。崩溃新key组两种服务都重复；stable-key-only仅unsafe重复；Runtime没有重复效果，unsafe5/5先NEEDS_REVIEW，再5次显式测试操作员确认。外部共61请求/51效果，15额外效果仅来自直接调用对照组。源码examples.reliability_comparison；原始数据、进程日志、审计均在当前report。局部L2/L3消融，不是完整Agent/生产故障率/框架排名。
3. **本人独立修改：SPEC_READY_USER_NOT_STARTED。** exercises/observation_diagnosis/README.md提供只读中断日志诊断任务与三个合成输入。未实现命令、未代写本人测试、MY_WORK仍空白；应由本人独立完成契约、失败测试、实现与解释。

协议与结论：docs/three_track_research.md。报告：reports/20261026T070223Z-three-track-research/FINAL_REPORT.md。包：delivery/20261026T070223Z-three-track-research/acceptance_bundle.tar.gz及同目录hash/校验结果。

上一轮20261026T032837Z-steady-load的212完整回归、270/270 steady成功、810事件与观察器强杀对账保持原样；新包history/携带其归档，里面仍保存更早179测试及独立矩阵/过载基线。当前没有重跑270负载或整个旧独立故障矩阵，不能合并分母。

下一步优先由本人开始练习，并在有明确模型与正预算授权后执行单dev真实smoke；随后独立复核证据。任何新实验必须新run/attempt，不覆写旧数据、不推送远端，不假定后台继续运行。启停服务仍按REPRODUCE.md使用已有授权Docker组会话。
