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

## Tool Error Recovery

本阶段完成 Agent 工具错误恢复机制

### 核心改动

1. tools.py
    - 工具成功统一返回 TOOL_OK
    - 工具失败统一返回 TOOL_ERROR
    - read_file 支持文件不存在、路径为空、路径是目录等错误判断
    - run_tool 遇到未知工具不再抛异常，而是返回 TOOL_ERROR

2. executor.py
    - 工具执行结果增加 success 字段
    - 根据 TOOL_ERROR 判断工具是否失败

3. prompts.py
    - 告诉 LLM 如何处理 TOOL_OK 和 TOOL_ERROR
    - 避免重复调用相同的 Action

4. agent.py
    - 增加重复 Action 检测
    - 防止 Agent 死循环

### 当前能力

- calculator(expression="...")
- read_file(path="...")
- write_file(path="...", content="...")
- 工具失败后可以给出自然语言解释


### V7.0 Agent Trace 日志系统

- Agent 执行过程的可观测
- Agent 行为可复盘
- 后续可以做统计分析
- 后续可以做 Agent Eval
- 后续可以做 LLMOps 监控

### V7.1 Agent Trace 日志系统 查看

- trace_view.py 脚本快速查看分析运行日志

### V7.2 Agent Trace 日志系统 分析

- trace_stats.py 脚本快速分析运行日志，Agent 行为可度量分析




