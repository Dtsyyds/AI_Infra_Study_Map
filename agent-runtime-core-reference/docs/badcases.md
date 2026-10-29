# Badcase 与未覆盖边界

| 观察 | 定位/处理 | 证据与剩余风险 |
|---|---|---|
| inspect 失败可能被当成“不存在” | DockerBackend.inspect_state 明确 UNKNOWN；只有精确 not-found 且 daemon 可达才 ABSENT | inspect 反例单测；真实容器清理 BLOCKED |
| 增加 repair 请求字段可能改变原文档幂等 digest | Store.submit 对 document_review 保留旧字段集合 | test_original_document_request_hash_stays_compatible |
| 全局 monkeypatch os.environ.get 干扰 pytest 自身 | Mock 限定到 Adapter 的局部模块对象 | 原 repair-storage 失败日志保留，retest 通过 |
| 重复故障测试触及真实每分钟受理限额 | 按数据库窗口显式等待，未放宽配额 | reliability/config.json + raw.jsonl |
| 模型/测试报告自述 PASS 可能误终结 | 独立 judge、nonce/身份/版本绑定、提交重算和 finish 二次门控 | tests/repair 与 test_repair_storage；真实 PASS 尚无 |
| SDK/版本环境文字陈旧 | environment.py 改为记录当前 Git/包/PG/权限事实 | 本轮 environment.json，不修改旧报告 |
| 新迁移探针存在 Optional 行与结果容器类型错误 | 显式检查 fetchone 结果与 Any 类型注解 | final-types 原失败保留，最终重跑结果另存 |

未知/未完：12 案例实际基线及人工参考修复；P0 A/C/F；容器正常/超时/资源/取消/强杀各 5 次；测试运行中与结果提交后退出；至少 1 次真实模型 DELIVERED；72 真实模型任务；270 负载提交与队列/执行/恢复分段时间。当前统计不宣称速度或修复率提升。模型接口只有录制/MockTransport 契约验证，账号支持、模型参数、计费与实时兼容性待授权核对。

当前上下文只为固定小文件提供 AST/白名单代码；不是全工程语义索引或动态调用图。没有面向超大仓库的相关性检索验证。基线未运行时 API 任务可失败为 BASELINE_NOT_CONFIRMED；其 NOT_RUN Artifact 是环境证据，不能当修复交付。

## 2026-10-25 追加修复

- orphan旧判断只查活跃Task时，未知标签也返回false，可能删除没有平台归属证据的容器；现返回None并保留，真实PG和协议反例已测。
- DELIVERED原门只匹配candidate_no/hash，补核对实际patch；验证启动绑定已DISPATCHED的正确工具/候选，模型预算预留也必须经过派发屏障。
- RepairContext增加候选/未决业务效果保护，兼容原v1快照并拒绝摘要删改；基准文件hash恢复时重算。
- 预算预留不能叫已调用模型；新增确认响应/未知预留/缺测token独立计数。API202后无终态或Artifact校验失败明确OBSERVATION_ERROR。
- 新增纯数据/传输反例与真实PG锁等待/非容器Worker优雅退出。可信runner新增测试中屏障，Manifest的runner hash同步更新，案例源码和gold不变。
- 容器25槽位、修复3恢复点、独立36次Harness qualification均有真实执行入口；预检仍BLOCKED，未执行样本不计次数。Framework映射见framework_mapping.md。
- P0 F真实分支现在准备并派发持久ToolCall后运行超时探针，具备Task/Attempt/Call关联；当前Docker阻塞，所以这一分支依然未实际运行。

## 2026-10-26 真实Docker验证发现

旧阻塞描述是历史状态；当前结果见TASK_STATUS.json。首次真实恢复测试失败为TOOL_FORBIDDEN：随机principal不在静态工具白名单。探针现使用demo-reviewer与唯一Task/Session，不改变生产策略。随后发现依赖导入预设ERROR日志抑制启动清理INFO记录，CLI显式配置日志后恢复证据可见。跨组会话/proc不可读导致服务停止误报及PID覆盖，也已修复并加入反例。

上述失败原件保留于reports/20261026T020248Z-no-paid-validation及本轮最终包补充报告。真实恢复仍使用确定性不修复候选，业务结果UNRESOLVED是预期，不能称真实模型修复成功。

- 真实负载发现诊断API的Artifact元数据缺少sha256，驱动器因此拒绝将任务成功当成完整观测成功。已在同一数据库只读快照返回encode(sha256(content),'hex')，补PG字节/hash及驱动器匹配/错配回归。首次78条OBSERVATION_ERROR和1个中断任务独立保留；新270条数据位于benchmarks/load-attempt-2，不覆盖、不混合分母。
