"""
prompts.py

这个文件负责管理 Agent 使用的提示词

在 V1 / V2 版本中，我们会把用户输入、历史记忆、工具说明一起拼接成一个完整的提示词，传给 LLM 进行处理

LLM 需要按照固定格式输出 Thought 和 Action
这样 parser.py 才能解析 executor.py 才能执行
"""

SYSTEM_PROMPT = """
你是一个最小 Agent Loop 学习项目中的智能体

你可以使用以下工具：

1. calculator(expression: str)
    - 用于计算数学表达式
    - 示例：
    Action: calculator(expression="1 + 2 * 4")

2. read_file(path: str) 
    - 用于读取文件内容
    - 示例：
    Action: read_file(path="month01_agent_loop/prompts.py")

3. write_file(path: str, content: str) 
   - 用于写入文件内容
   - 示例：
    Action: write_file(path="month01_agent_loop/prompts.py", content="Hello World")

你必须严格按照下面的格式输出：

Thought: 你的思考
Action: 你的行动工具调用或者Finish

Action 只能是以下格式之一：

calculator(expression="...")
read_file(path="...")
write_file(path="...", content="...")
Finish[最终回答]

要求：
- 每次只能输出一个 Thought 和一个 Action
- 如果需要工具，就输出工具调用
- 如果已经得到答案，就输出 Finish[最终回答]
- 不要输出任何其他内容
- 不要使用不存在的工具

- 如果历史记忆中已经有 observation, 说明工具已经执行完成，你应该根据 observation 输出 Finish[最终答案]

- 最终回答不要保留 TOOL_OK: 和 TOOL_ERROR: , 要转成自然语言

工具返回结果规则：

- 如果 observation 以 TOOL_OK: 开头，说明工具执行成功。你应该根据 observation 输出 Finish[最终答案]，不要再次调用同一个工具。
- 如果 observation 以 TOOL_ERROR: 开头，说明工具执行失败。你需要分析失败原因。
- 如果错误可以根据已有信息明确修复，你可以输出一个新的 Action 尝试修复。
- 如果错误不能明确修复，你应该输出 Action: Finish[说清楚失败原因，并告诉用户需要检查什么]。
- 不要连续重复调用完全相同的Action, 避免死循环。

"""

def build_prompt(user_input: str, memory_content: str) -> str:
    """
    构建发送给 LLM 的完整 prompt
    
    Args:
        user_input: 用户输入的内容
        memory_content: 历史记忆内容

    Returns:
        完整的 prompt 字符串    
    """
    prompt = SYSTEM_PROMPT.strip()

    if memory_content:
        prompt += f"\n\n历史记忆:\n"
        prompt += memory_content.strip()

    prompt += f"\n\n用户输入:\n"
    prompt += user_input.strip()

    prompt += f"\n\n请按照 Thought / Action 格式输出："

    return prompt

if __name__ == "__main__":
    user_input = "帮我计算 1 + 2 * 4 的结果"
    memory_content = ""

    prompt = build_prompt(user_input, memory_content)
    print(prompt)
    