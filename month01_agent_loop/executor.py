"""
这个文件负责联立 parser.py 和 tools.py

流程：

1. 接收一条 Action 指令
2. 使用 parse_action 解析
3. 调用对应的执行工具
4. 如果是 Finish 就返回最终答案

"""

from parser import parse_action
from tools import *

cal_text = f"Action: calculator(expression=\"1 + 2 * 4\")"
read_text = f"Action: read_file(path=\"./README.md\")"
write_text = f"Action: write_file(path=\"./test.txt\", content=\"hello world!\")"
finish_text = f"Action: Finish[任务完成]"

def execute_action(action_text: str) -> dict:
    action = parse_action(action_text)

    if action["type"] == "error":
        return {
            "type": "error",
            "content": action["message"]
        }
    if action.get("type") == "Finish":
        return {
            "type":"Finish",
            "content": "最终答案"
        }
    else:
        tool_name = action["tool_name"]
        args = action["args"]
        observation = run_tool(tool_name, **args)
        
        return {
            "type": "observation",
            "content": observation
        }
    return {
        "type": "error",
        "content": "未知错误"
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