# Month04 面试讲解

## 30 秒项目介绍

我实现了一个异步 Agent 服务可靠性原型，重点解决高并发请求下的容量控制、排队、超时、客户端断连、流式背压和资源清理。服务使用三层容量模型限制 active、running 和 waiting 请求；用请求级状态机和唯一终结者解决完成、超时与取消之间的竞态；通过有界队列实现流式背压；最后用 Prometheus 指标和异步压测验证生命周期与容量边界。

## 3–5 分钟完整讲解

### 1. 背景

Agent 请求通常比普通 HTTP 请求持续时间长，而且会同时持有模型执行、队列、后台 Task 和网络连接等资源。只调用一次模型并返回结果比较容易，困难的是请求超时、客户端断连或流式发送失败时，所有资源能否只释放一次，并且指标还能反映真实状态。

### 2. 容量控制

我设计了三层容量：

- `max_active` 限制完整 ASGI 请求生命周期，包括路由执行、响应发送和清理。
- `max_running` 限制真正持有执行 permit 的 Agent 数量。
- `max_waiting` 限制服务内部允许等待执行名额的请求数量。

active 满时立即返回 `503 active_limit`；active 未满但执行和等待容量都满时返回 `503 capacity_exceeded`。这样可以区分 HTTP 层过载与内部执行队列过载。

### 3. 生命周期竞态

同一个请求可能同时发生业务完成、Deadline 到达、客户端断连或内部异常。如果每条路径都各自取消任务和释放 Semaphore，就会产生重复释放、状态覆盖或 Gauge 变成负数。

因此我把请求状态定义为：

```text
ACTIVE -> FINALIZING -> FINALIZED
```

所有终结事件都调用同一个 `try_claim_finalize()`。它使用 `asyncio.Lock` 原子地完成状态检查和迁移，只有一个调用者能成为终结者。锁只保护短暂状态变更，不在持锁期间等待后台任务，从而避免死锁。

唯一终结者发出取消后，会 `await` 后台任务真正退出，然后再释放 queue slot 和 execution permit。因为 `task.cancel()` 只是投递取消请求，并不代表协程的 `finally` 已执行完成。

### 4. 流式背压

流式接口中，生产者和网络发送者通过 `asyncio.Queue(maxsize=8)` 解耦。客户端慢时，发送变慢，队列填满，生产者阻塞在 `await queue.put()`，从而把压力反向传递到生产端，防止 Token 在内存中无限累积。

我还区分了三个超时：业务 Deadline、单次 `send()` 超时和整个响应 Deadline。单次发送超时不能替代总响应 Deadline，因为每一次发送都可能没有超时，但整个流仍然拖得非常久。

### 5. 可观测性

我使用：

- Gauge 表示当前 active、running、waiting 请求数；
- Counter 表示业务终态和容量拒绝累计次数；
- Histogram 表示 HTTP 总耗时、排队时间和生产者生命周期。

指标记录点和资源状态绑定。例如 running Gauge 只在真正获得 permit 后增加，并在后台任务完成清理、permit 真正释放时减少。排队时被取消的请求会记录 cancelled 等待样本，但没有进入生产阶段的请求不会记录 `0 s` 生产者样本。

### 6. 实验结果

在 `max_running=2`、每个模拟任务约 `0.5 s` 时，理论稳定业务吞吐约为 `4 req/s`。12 个请求同时到达时：

- 内部容量先满的配置得到 5 个成功、7 个 `capacity_exceeded`；
- active 层先满的配置得到 4 个成功、8 个 `active_limit`。

这个实验说明增大等待队列可以吸收突发，但不会提高稳定吞吐。同时，总体延迟 P50 会被快速失败的 `503` 拉低，所以压测必须按结果拆分延迟，并单独报告成功吞吐率和拒绝率。

### 7. 项目边界

当前实现是单进程原型，Agent 使用模拟任务，实验结果不能代表真实 GPU 推理性能。下一步需要在真实推理链路中增加 Token、Prefill、Decode 和 GPU Kernel Profiling，并进一步处理多 Worker 下的容量与 Prometheus 聚合。

## 高频追问

### 为什么需要三层限制，只有一个 Semaphore 不行吗？

一个 Semaphore 只能描述一种资源。执行并发限制的是模型或计算资源；等待容量限制的是内存和排队时延；active 限制还要覆盖已经结束生产但仍在发送响应或清理的连接。三者生命周期不同，必须分开建模。

### 为什么排队不能提高稳定吞吐？

稳定吞吐主要由执行并发和单任务服务时间决定：

```text
throughput ≈ max_running / average_service_time
```

队列改变的是突发请求被拒绝还是等待，以及等待时间和内存占用，不会增加执行资源。

### 为什么 `task.cancel()` 后不能立即释放 permit？

取消是协作式的。`cancel()` 只是让协程在下一个可取消点收到 `CancelledError`；协程可能还要执行异步生成器关闭、上下文管理器退出和 `finally` 清理。提前释放 permit 会让新任务进入，而旧任务实际上仍占用资源。

### 为什么 `state_lock` 不能包住完整清理过程？

清理包含 `await task` 等不确定时长操作。持锁等待会阻塞其他参与者读取或发布状态，甚至让被等待任务间接等待同一把锁，形成死锁。锁只保护“谁获得终结权”这一小段原子状态迁移。

### 为什么业务结果 Counter 要在清理完成后增加？

收到完成或取消事件时，请求还可能持有后台 Task、Semaphore 和队列名额。如果此时记录完成，指标会声称请求已经结束，但资源仍未释放。Counter 应表达真正完成生命周期的请求。

### 为什么生产者耗时不是模型纯计算时间？

流式生产者生命周期可能包含生成器调度、Token 入队、队列背压和生成器关闭。生成和网络发送还会重叠。因此它是生产者生命周期指标，不是 GPU Kernel 时间。模型计算需要在更内层通过 CUDA Event 或 GPU Profiler 测量。

### 为什么不能把排队、生产者和发送耗时直接相加？

它们的边界不一定互斥。特别是流式场景中，生产与发送并行；生产者还可能因为队列满而等待消费者。直接相加会重复计算重叠区间。

### 为什么 Counter 看起来一直增加？

Counter 表示进程启动后的累计事件数，不表示当前状态。当前并发量使用 Gauge；速率通常通过 PromQL 对 Counter 使用 `rate()` 计算。

### 为什么总体 P50 可能具有误导性？

容量拒绝通常在几毫秒内返回，而成功请求可能需要一秒。如果把两者混在一起，快速失败占多数时，总体 P50 会非常低，但这并不代表成功请求变快。应该按结果拆分延迟。

### `active_limit` 与 `capacity_exceeded` 有什么区别？

- `active_limit`：请求没有获得外层 active slot，没有进入路由业务生命周期。
- `capacity_exceeded`：请求获得了 active slot，但执行 permit 和 queue slot 都没有容量。

### 为什么流式超时后 HTTP 状态仍可能是 200？

HTTP 响应头一旦发送就不能修改。若超时发生在部分 Token 已发送之后，只能在流内的最终 `done` 事件表达业务结果，或者由连接异常表达传输失败。

## 简历表述

可以写成：

> 基于 FastAPI/asyncio 实现 Agent Runtime 服务原型，设计 active/running/waiting 三层容量控制与唯一终结状态机，处理超时、断连、流式背压和取消清理竞态；接入 Prometheus 生命周期指标并编写异步压测，验证容量拒绝、尾延迟与约 4 req/s 理论吞吐边界。

不要写“实现了生产级分布式调度”或“完成真实 LLM GPU 优化”，因为当前项目还没有覆盖这些范围。

## 面试前自测

不看代码，尝试回答：

1. 一个请求从进入中间件到释放 active slot 经历了哪些阶段？

请求进入 ASGI Middleware
→ 判断是否为受保护接口
→ 开始记录 HTTP 总耗时
→ 检查 active 容量
    ├─ 已满：记录 active_limit，返回 503
    └─ 未满：获得 active slot，active Gauge +1
→ 进入路由
→ 创建 RequestContext
→ AdmissionController.try_admit()
    ├─ execution permit 可用：running Gauge +1，直接执行
    ├─ execution 满但 queue slot 可用：waiting Gauge +1，等待
    └─ 两者都满：记录 capacity_exceeded，返回 503
→ 排队请求获得 execution permit
    → running Gauge +1
    → 释放 queue slot，waiting Gauge -1
→ Agent 生产
→ 完成/超时/断连/异常竞争终结权
→ 唯一终结者等待清理完成
→ 释放 execution permit，running Gauge -1
→ 发送完整响应
→ self.app(...) 完整返回
→ 释放 active slot，active Gauge -1
→ 记录 HTTP 总耗时

2. 完成和 Deadline 同时到达时，为什么只能有一个终结者？

如果每条路径都各自取消任务和释放 Semaphore，就会产生重复释放、状态覆盖或 Gauge 变成负数。
因此我把请求状态定义为：

```text
ACTIVE -> FINALIZING -> FINALIZED
```

所有终结事件都调用同一个 `try_claim_finalize()`。它使用 `asyncio.Lock` 原子地完成状态检查和迁移，只有一个调用者能成为终结者。锁只保护短暂状态变更，不在持锁期间等待后台任务，从而避免死锁。

唯一终结者发出取消后，会 `await` 后台任务真正退出，然后再释放 queue slot 和 execution permit。因为 `task.cancel()` 只是投递取消请求，并不代表协程的 `finally` 已执行完成。

asyncio.Lock 不是用来保护整个清理过程，而是原子地完成“检查 ACTIVE 状态并迁移到 FINALIZING”。获得迁移权的协程成为唯一终结者；其他协程不能重复取消、释放资源或覆盖终态。

3. 排队取消时，waiting Gauge 在哪里归零？

waiting Gauge 在 AdmissionController.wait_for_execution() 的 finally 中，通过 release_queue_slot(ctx) 归零。这样排队成功和排队取消两条路径都会退出 waiting 状态

4. 为什么慢客户端最终会让生产者阻塞？

客户端慢,发送变慢,导致队列堆积,生产者阻塞在 await queue.put()，从而把压力反向传递到生产端，防止 Token 在内存中无限累积

客户端读取变慢
→ ASGI send() 变慢
→ Consumer 取 Token 变慢
→ 有界队列逐渐填满
→ Producer 阻塞在 await queue.put(token)
→ 生产速度被消费速度限制

对于有界队列：
- 队列未满：立即放入。
- 队列已满：当前协程挂起。
- Consumer 取走元素：生产者重新获得运行机会。
这就是背压，不需要额外轮询队列长度。

5. 哪些指标可以相互校验，哪些指标不能直接相加？

在单进程、没有正在处理的请求，并且进程没有重启时，可以校验：

agent_http_request_duration_seconds_count=sum(agent_request_outcomes_total)+sum(agent_admission_rejections_total)

容量 Gauge 可以校验范围：
0 <= active_requests <= max_active
0 <= running_requests <= max_running
0 <= waiting_requests <= max_waiting

系统空闲后应满足：
active_requests == 0
running_requests == 0
waiting_requests == 0

以下内容不能直接相加：
- HTTP 总耗时和生产者耗时
- 生产者耗时和发送耗时
- 不同 Histogram 的 P95
- Gauge 当前值和 Counter 累计值
- 不同结果集合的平均延迟
原因包括：
1. 生产和发送会重叠。
2. 生产者可能因背压等待 Consumer。
3. 不同 Histogram 的样本集合不同。
4. 分位数本身不可加。
5. Gauge 是瞬时状态，Counter 是累计事件。

6. 如果部署 4 个 Uvicorn Worker，现有容量和指标语义会发生什么变化？

每个 Worker 的进程内容量独立，因此理论总执行容量可能扩大四倍，但它不是严格的全局容量限制，实际利用率取决于负载均衡；现有 Prometheus 指标也会从服务级语义退化为 Worker 级语义。
