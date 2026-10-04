当前 LLMAgent 中哪些字段会被 A、B 竞争访问？可能出现什么错误？
为什么把接口声明成 async def，并不代表 agent.run() 已经能够并发？
请把下面对象分成“请求独占”和“服务共享”：
Memory
AgentTrace
action_history
Tool Registry
LLM Client
request_id
Semaphore
Deadline
cancellation event
A 断开后，系统应该按照什么顺序停止任务和释放资源？

在当前上传代码中，真正危险的是：

self.memory：A、B 的消息和 Observation 会混在一起，甚至形成跨用户数据泄漏。
self.last_trace：后到请求会覆盖先到请求的 Trace。
如果未来把 action_history、task_memory 移到实例属性，也会产生同样问题。

max_steps、step_timeout_seconds、llm_timeout_seconds 如果初始化后只读，只是共享配置，不会因为多个请求读取就产生竞态。只有运行时修改它们才需要同步。

async def 只有执行到真正可等待且能让出事件循环的 await 时，才会获得并发能力。

| 请求独占                 | 服务共享                 |
| -------------------- | -------------------- |
| Memory / task memory | 只读 Tool Registry     |
| AgentTrace           | 并发安全的 LLM Client/连接池 |
| action_history       | Semaphore            |
| request_id           | Metrics Registry     |
| Deadline             | Request Manager      |
| cancellation event   | 全局有界队列               |

如果以后需要会话记忆，可以共享一个持久化 Memory Store，但读取和写入必须使用：

tenant_id + session_id

进行隔离；每次运行使用的工作记忆仍然是请求独占的。

推荐顺序是：

原子抢占终结权：ACTIVE → FINALIZING。
冻结请求，禁止创建新工具调用或子任务。
设置请求级取消信号。
取消后台 Task，并向支持取消的 LLM/RPC/工具传播取消。
await 在途任务退出，设置清理超时。
丢弃迟到结果，回滚或释放请求占用的 KV、缓冲区和队列槽位。
最后释放 Semaphore Permit。
完成 Trace 和指标，发布 FINALIZED 并唤醒等待者。

需要注意：Task.cancel() 只是注入 CancelledError，不保证任务立即停止。尤其是：

await asyncio.to_thread(blocking_tool)

取消协程并不能终止线程里的同步工具。因此必须允许它稍后返回，并通过终态检查丢弃迟到结果。

Permit 最后释放的真正原因是：Permit 代表系统对外宣布“已有容量可用”。如果过早释放，新请求可能进入并复用尚未清理的队列槽位、KV或缓冲区。

第一个合法到达状态临界区的终结事件获胜。

完成先提交：SUCCEEDED
Deadline 先到：TIMED_OUT
断开先被观察到：CANCELLED
内部异常先发生：FAILED
后续事件只能读取既有终态，不能抢占或覆盖

固定优先级最多只能作为“同一临界区内完全同时事件”的决胜规则，不能推翻已经开始的终结流程。