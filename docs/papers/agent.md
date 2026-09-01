1. Runtime 编排必须保证 at-most-once dispatch，即一次 Action 在当前进程内至多提交一次工具调用。线程超时后是否产生了外部副作用仍可能不确定，因此后续重试还需要幂等键，而不能简单地再次调用工具。

2. ToolRuntime 和单步 timeout 是 Agent 级配置，ExecutionContext 是请求级状态。Agent Loop 将这些控制对象向下传给 executor，而 executor 只返回 observation、finish 或稳定错误结果，不能通过业务返回值反向修改 Runtime 配置。

### 三层职责
LLM：根据上下文生成下一步动作建议，本身不执行工具。
Agent Loop：组装 Prompt、调用 LLM、解析 Action、执行工具、记录 Observation。
Agent Runtime：决定动作是否允许执行，并控制 deadline、取消、超时、并发和错误契约。

operation
   ↓ submit
Future
   ↓ result(timeout)
成功结果 / FutureTimeoutError
   ↓
ToolExecutionTimeoutError
future.result(timeout) 只是调用方停止等待，Future 不会被标记为超时，工作线程也不一定停止。

LLMAgent
  context/runtime/timeout
           ↓
execute_action
           ↓
type/content/success

max_steps 和 deadline 分别限制什么？
为什么时间预算使用 monotonic()？
总预算 10 秒、已用 8 秒、单步上限 5 秒，有效 timeout 是多少？
future.result(timeout) 后 Future 和工作线程分别是什么状态？

max_steps 限制agent loop 思考执行次数，deadline 任务执行总时间。
monotonic() 避免系统时间因为 NTP 调整而倒退。
有效 timeout 为 2s
future.result(timeout) 的等待方收到 TimeoutError，Future 本身通常仍处于 RUNNING，工作线程继续执行。
