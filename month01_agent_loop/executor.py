"""
executor.py

这个文件负责连接 parser.py 和 tools.py。

流程：
1. 接收一条 Action 指令
2. 使用 parse_action 解析
3. 如果是工具调用，就执行对应工具
4. 如果是 Finish，就返回最终答案
5. 如果解析失败，就返回错误信息
"""

from parser import parse_action
from tools import run_tool


def execute_action(action_text: str) -> dict:
    """
    执行一条 Action 指令。

    Args:
        action_text: 例如：
            Action: calculator(expression="1 + 2 * 4")
            Action: read_file(path="./test.txt")
            Action: write_file(path="./test.txt", content="hello world")
            Action: Finish[任务完成]

    Returns:
        {
            "type": "observation" | "finish" | "error",
            "content": "工具返回结果 / 最终答案 / 错误信息"
        }
    """

    action = parse_action(action_text)

    if action["type"] == "error":
        return {
            "type": "error",
            "content": action["content"]
        }

    if action["type"] == "finish":
        return {
            "type": "finish",
            "content": action["content"]
        }

    if action["type"] == "tool":
        tool_name = action["tool_name"]
        args = action["args"]

        observation = run_tool(tool_name, **args)

        # return {
        #     "type": "observation",
        #     "content": observation
        # }

        success = not observation.startswith("TOOL_ERROR:")

        return {
            "type": "observation",
            "content": observation,
            "success": success
        }

    return {
        "type": "error",
        "content": f"未知解析类型: {action['type']}"
    }


if __name__ == "__main__":
    test_cases = [
        'Action: calculator(expression="1 + 2 * 4")',
        'Action: write_file(path="./test.txt", content="hello world!")',
        'Action: read_file(path="./test.txt")',
        'Action: Finish[任务完成]',
        'Action: unknown_tool(value="test")',
        'hello world',
    ]

    for text in test_cases:
        print("=" * 60)
        print("输入:", text)
        result = execute_action(text)
        print("执行结果:", result)