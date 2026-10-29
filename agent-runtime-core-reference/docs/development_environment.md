# 开发机器与资源预算

2026-10-29只读检查。以下配置是针对本工程“云端模型API + 本地Python沙箱”的开发估算，不是实测容量承诺或Docker官方最低配置。

| 项目 | 可开始开发 | 推荐 | 本机观测 |
|---|---|---|---|
| CPU | 4核级别，串行验证 | 6–8核 | Ryzen 7 4800U，8核16线程 |
| 内存 | 8 GB精简运行 | 16 GB可开发，32 GB便于多任务 | 精确读取/proc为14.99 GiB；检查时MemAvailable约9.3 GiB |
| 空闲磁盘 | 至少20 GiB作为开发准备目标 | 30–50 GiB，随镜像和数据增加 | 同一分区约6.7 GiB可用、使用98% |
| GPU | 云端API方案无需独显 | 无需为本项目购买GPU | 未做GPU能力评估 |
| 系统 | 支持项目依赖和Docker的Linux | 沿用现有Ubuntu | Ubuntu 24.04.3 LTS x86_64 |

Docker官方列出Ubuntu 24.04 LTS及amd64的支持：[安装前提](https://docs.docker.com/engine/install/ubuntu/)。本机Docker Engine 29.9.1 client/server只读握手成功。普通会话socket访问失败，使用已经授权的 `sg docker` 组会话成功；未改组、socket权限或daemon。握手成功不等于本次重跑沙箱验收。

项目目录约2.2 GiB，其中reports约31 MiB、delivery约64 MiB、venv约239 MiB、Python环境约224 MiB、PostgreSQL二进制约161 MiB、数据库约94 MiB。报告及历史验收包占比较小，应保留。没有扫描或删除其他项目和个人目录；磁盘清理或迁移需要先明确对象。`/tmp`与项目位于同一分区，改用/tmp不会增加空间。Docker数据目录的分区尚未核实，预检仅检查工作区分区。

Python现有版本3.11.17；Node为20.19.6、npm为10.8.2。Node仅记录版本，前端选型时另核对支持窗口和依赖要求，本轮未安装或升级。

## 起步运行建议

首阶段采用一个活跃修复任务、一个验证容器；前端/API/PG/Worker按需启动。保留现有容器资源限制，监控实际峰值后再提高并发。这是开发运行建议，尚未新增全局限流配置，也不修改现有配额或历史负载口径。暂不常驻整套监控服务、不下载本地大模型。

API模型推理在服务端执行，本机处理API请求、持久化、代码上下文及候选测试。不做模型训练。未来若改为本地推理，需要依据模型规模、量化和上下文单独测算显存/内存与速度。

## 只读预检

```bash
make preflight
.venv/bin/python -m examples.dev_preflight --json
# 在已授权的Docker组会话内检查daemon
sg docker -c '.venv/bin/python -m examples.dev_preflight --docker --json'
```

预检不读取.env、不请求模型、不启动数据库或容器。READY只表示当前轻量检查满足开发建议，不能替代集成验收；未测Docker或读取不到内存时保留CAUTION。内存来自宿主/proc，不计算cgroup配额；不能用于证明容器环境的容量。

磁盘小于5 GiB为BLOCKED，5–20 GiB为CAUTION；这些是开发提示阈值，不是现有Runtime调度策略。默认BLOCKED退出2，CAUTION退出0；`--strict`下CAUTION/未检查项也退出2。预检不会自动修复、清理或更改权限。
