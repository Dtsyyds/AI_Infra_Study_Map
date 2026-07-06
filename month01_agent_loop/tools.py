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
import os
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
        if not expression:
            return tool_error("计算失败: expression 不能为空")

        result = simple_eval(expression)
        return tool_ok(str(result))

    except Exception as e:
        return tool_error(f"计算失败: {e}")

def read_file(path: str) -> str:
    """
    读取文本文件工具
    Args:
        path: 文件路径
    Returns:
        文件内容字符串
    """
    try:
        if not path:
            return tool_error("读取失败：path 不能为空")
        if not os.path.exists(path):
            return tool_error(f"读取失败: 文件不存在: {path}")
        if os.path.isdir(path):
            return tool_error(f"读取失败: 当前路径是目录，不是文件: {path}")
    
        with open(path, "r", encoding="utf-8") as f:
            return tool_ok(str(f.read()))
    except Exception as e:
        return tool_error(f"读取失败: {e}")
    
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
        if not path:
            return tool_error("写入失败：path 不能为空")
        if not content:
            return tool_error("写入失败：content 不能为空")
        # if not os.path.exists(path):
        #     return tool_error(f"写入失败，文件不存在：{path}")
        if os.path.isdir(path):
            return tool_error(f"写入失败：当前路径是目录而不是文件: {path}")
        
        dir_name = os.path.dirname(path)    # 如果目录不存在，他会自动创建目录

        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        
        with open(path, "w") as f:
            # return tool_ok(str(f.write(content)))  # 返回写入的字符数
            f.write(content)
        return tool_ok(f"写入成功: {path}") 
    except Exception as e:
        return tool_error(f"写入失败: {e}")
    
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

# 新增辅助函数，统一工具返回格式
def tool_ok(content: str) -> str:
    return f"TOOL_OK: {content}"

def tool_error(content: str) -> str:
    return f"TOOL_ERROR: {content}"

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
        return tool_error(f"未知工具：{tool_name}")
    
    tool = TOOLS[tool_name]

    try:
        result = tool(**kwargs)
        return str(result)
    except TypeError as e:
        return tool_error(f"工具参数错误: {e}")
    except Exception as e:
        # print(e)
        return tool_error(f"工具执行失败：{e}")
    
if __name__ ==  "__main__":
    # print("当前可用工具:", get_tool_name())

    # print("\n测试工具:calculator")
    # print(run_tool("calculator", expression="1 + 2 * 4"))

    # print("\n测试工具:read_file")
    # print(run_tool("read_file", path="./README.md"))

    # print("\n测试工具:write_file")
    # print(run_tool("write_file", path="./test.txt", content="hello world!"))

    # print("\n测试不存在的工具:")
    # print(run_tool("unknown_tool", value="test"))

    # print("\n测试参数错误")
    # print(run_tool("calculator", "1 + 2 * 3"))

    print(run_tool("calculator", expression="1 + 2 * 4"))
    print(run_tool("read_file", path="./not_exist.txt"))
    print(run_tool("write_file", path="./tmp/v5_test.txt", content="hello v5"))
    print(run_tool("read_file", path="./tmp/v5_test.txt"))
    print(run_tool("unknown_tool"))
    