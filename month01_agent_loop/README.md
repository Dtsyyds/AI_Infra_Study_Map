# Agent 最小架构 就是一个带工具的 while 循环
# 本质就是 构造 prompt 然后调用 LLM 然后解析 LLM 的输出 然后调用工具函数 然后返回结果 循环直到结束
`
## 当前系统架构：

main.py -> agent.py -> prompt.py -> llm.py -> parser.py -> executor.py -> tools.py -> memory.py
                    构造 prompt     生成 Thought / Action 解析 Action 执行工具函数          记录历史

## 标准 ReAct Agent 架构：

LLM 输出 Action
- 工具执行得到 Observation
- Observation 再返回 Prompt
- LLM 再思考
- LLM 输出 Finish

Thought -> Action -> Observation -> Thought -> Finish

## Robust Real LLM ReAct Agent

本阶段完成真实 LLM 的输出解析增强

parser 增强 Action 解析逻辑，支持中文冒号，支持工具调用解析，支持多参数解析，支持 Finish 解析，新增错误处理兜底

agent 增强 LLM 输出的 Action 提取逻辑，支持中文冒号，支持 Markdown 代码块提取 Action, 支持全文提取 Action，支持无 Action 兜底处理



