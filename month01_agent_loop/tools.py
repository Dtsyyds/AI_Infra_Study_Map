"""
tools.py

这个文件负责定义 Agent 可以调用的工具

第一版先实现最基础的三个工具：
1. calculator: 计算数学表达式
2. read_file: 读取文本文件
3. write_file: 写入文本文件

注意：
- 工具函数统一返回字符串，方便后续作为 Obersvation 交给 LLM
- 当前 calculator 使用 eval加, 仅学习 demo

"""

from typing import Callable, Dict, List
from simpleeval import simple_eval

def calculator(expression: str) -> str:
    """
    简单计算器工具
    Args:
        expression: 数学表达式
    Returns:
        计算结果字符串
    """
    try:
        result = eval(expression)
        return result
    except Exception as e:
        return f"计算失败: {e}"

def read_file(path: str) -> str:
    """
    读取文本文件工具
    Args:
        path: 文件路径
    Returns:
        文件内容字符串
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return str(f.read())
    except Exception as e:
        return f"读取失败: {e}"
    
def write_file(path: str, content: str) -> str:
    """
    写入文本文件工具
    Args:
        path: 文件路径
        content: 写入内容
    Returns:
        写入结果字符串
    """
    try:
        with open(path, "w") as f:
            f.write(content)
        return f"写入成功: {path}" 
    except Exception as e:
        return f"写入失败: {e}"
    
TOOLS: Dict[str, Callable] = {
    "calculator": calculator,
    "read_file": read_file,
    "write_file": write_file,
}

def get_tool_name() -> List[str]:
    """
    返回当前注册的工具名称列表
    """
    return list(TOOLS.keys())

def run_tool(tool_name: str, **kwargs) -> str:
    """
    根据工具名称运行对应的工具

    Args:
        tool_name: 工具名称
        **kwargs: 工具参数
    Returns:
        工具执行结果
    """
    if tool_name not in TOOLS:
        raise ValueError(f"Tool {tool_name} not found")
    
    tool = TOOLS[tool_name]

    try:
        result = tool(**kwargs)
        return result
    except Exception as e:
        print(e)
        return ""
    
if __name__ ==  "__main__":
    print("当前可用工具:", get_tool_name())

    print("\n测试工具:calculator")
    print(run_tool("calculator", expression="1 + 2 * 4"))

    print("\n测试工具:read_file")
    print(run_tool("read_file", path="./README.md"))

    print("\n测试工具:write_file")
    print(run_tool("write_file", path="./test.txt", content="hello world!"))

    print("\n测试不存在的工具:")
    print(run_tool("unknown_tool", value="test"))

    print("\n测试参数错误")
    print(run_tool("calculator", "1 + 2 * 3"))
    