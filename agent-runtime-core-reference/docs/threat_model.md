# 受控修复威胁模型

范围：本地教学参考工程、可信平台/数据库/Manifest/runner、可能错误或越界的模型输出、固定合成 Python 案例。不是强多租户服务。Docker 与真实模型验证目前 BLOCKED，见根目录 blockers.md。

## 信任边界

- API 凭据决定 principal，模型仅返回 Patch DTO；TaskManifest、镜像、测试、quota、deadline、身份不由候选指定。默认拒绝 tests、conftest、依赖锁、pytest 配置、平台与迁移修改。
- 主进程访问 PostgreSQL/MCP/受控模型端点。候选只在固定 digest 的无网络、非 root、只读根、cap-drop、no-new-privileges、资源受限容器执行，无宿主 socket/卷挂载。源码写入容器 tmpfs 后运行；容器不继承模型密钥。
- RunnerReport 是不可信输入。控制器 RunRecord 与当前 VerificationIdentity/nonce/测试集合/文件 hash/退出状态/清理证据联合决定 Verdict。未知清理不等于成功。日志不是恢复事实源。
- 参数、补丁、报告与 Artifact 有独立尺寸限制；Artifact 原有单件 2 MiB、任务 8 MiB、平台逻辑 512 MiB 事务限额保留。核心证据超限不能静默截断为 PASS。

## 明确的限制

业务源码和 unittest runner 在同一 Python 解释器运行。攻击者若能够任意执行恶意 Python，可能修改测试运行时、观察 nonce 或伪造自身输出；路径白名单、保护文件 hash 和外部运行记录不构成对任意恶意候选的证明。受保护测试目前是 tmpfs 上的文件，依靠修改范围校验与前后 hash 检查，并非操作系统不可写隔离。要支持敌对代码，需要不同的可信观测机制与更强沙箱评估，超出本轮范围。

文件 hash 证明一致性，不证明来源真实性。报告包无第三方签名。PostgreSQL 管理员、宿主管理员及可信服务进程不在敌对模型内。API 本地 demo 身份不是生产 SSO/RBAC。无跨 Task 长期记忆，无 Serverless 弹性承诺。

Lease/fence 拒绝过期写回，不能撤销已发出的外部 HTTP 请求；UNKNOWN 与人工 reconciliation 保留。验证缓存关闭；重跑物理测试生成新 verification_run_id，同一逻辑调用仍用原 tool_call_id。

## 当前验证范围

inspect 权限/daemon/解析错误、旧 PASS、task/candidate/nonce/hash 错配、skip/空测试、篡改/缺失报告、取消后 DELIVERED、旧 Attempt 写回、预算耗尽都有自动化检查。真实 Docker 终止/归属清理、资源耗尽和恶意解释器行为尚未得到本轮容器证据。不能由单测绿色推断这些性质成立。
