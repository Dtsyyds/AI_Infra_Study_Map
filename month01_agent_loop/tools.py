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

# 当前项目根目录：AI_INFRA_STUDY_MAP
# tools.py 位于 month01_agent_loop/tool.py
WORKSPACE_ROOT =  os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..')       # 获取当前 Python 脚本所在目录的父目录的绝对路径，并将结果赋值给变量
    # __file__ 表示当前文件 tools.py 的路径
    # os.path.dirname(__file__) 表示当前 Python 脚本所在的目录的绝对路径，得到 tools.py 所在的目录
    # os.path.join(os.path.dirname(__file__), '..') 拼接上一层目录，得到相对路径指向 month01_agent_loop 的父目录
    # os.path.abspath() 将相对路径转换为绝对路径，得到项目根目录
)
SENSITIVE_TOOLS = {
    ".env", # 环境变量
    ".gitignore", # git 忽略文件
    ".git",
    ".idea",
    ".ssh",
    ".aws",
    ".config",
    "id_rsa",
    "id_rsa_pub",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".crt",
    ".p12",
    ".pfx",
}

# 增加路径解析函数
def resolve_workspace_path(path: str) -> tuple[bool, str, str]:
    """
    将用户传入的路径解析成 workspace 内的绝对路径。

    Returns:
        {success, abs_path, error_message}

    success=True:
        abs_path 是合法的 workspace 内路径

    success=False:
        error_message 是错误信息
    """
    if not path:
        path = "."

    path = path.strip()

    # 不展开 ~ 开头的路径，避免访问用户主目录
    if path.startswith("~"):
        return False, "", f"路径越界：不允许使用用户主目录路径：{path}"
    
    # # 如果是绝对路径，直接标准化
    # if os.path.isabs(path):
    #     abs_path = os.path.realpath(path)
    # else:
    #     abs_path = os.path.realpath(os.path.join(WORKSPACE_ROOT, path))

    # 不要只规范化候选路径，先规范候选路径
    workspace_root = os.path.realpath(WORKSPACE_ROOT)

    if os.path.isabs(path):
        candidate_path = path
    else:
        candidate_path = os.path.join(WORKSPACE_ROOT, path)

    abs_path = os.path.realpath(candidate_path)

    # # 判断是否仍在 workspace 内
    # if abs_path != WORKSPACE_ROOT and not abs_path.startswith(WORKSPACE_ROOT + os.sep):
    #     return False, "", f"路径越界：只能访问项目目录内部：{path}"
    # 使用路径语义判断
    try:
        inside_workspace = (
            os.path.commonpath([workspace_root, abs_path])
            ==workspace_root
        )
    except ValueError:
        inside_workspace = False

    if not inside_workspace:
        return False, "", (
            f"路径越界：只能访问项目目录内部：{path}"
        )
    
    return True, abs_path, ""
"""
abspath() 和 realpath() 的区别

假设 /workspace/link.txt 是符号链接，指向 /etc/passwd
使用 abspath() 会得到 /workspace/link.txt 它只做字符串层面的处理，消除 . 处理 .. 不跟踪符号链接
使用 realpath() 会得到 /etc/passwd 它会继续解析符号链接，得到操作系统最终访问的真实路径。
"""
def to_workspace_display_path(abs_path: str) -> str:
    """
    将绝对路径转换成相对 workspace 的展示路径，避免把本机的绝对路径暴露给 LLM。
    """
    workspace_root = os.path.realpath(WORKSPACE_ROOT)
    real_abs_path = os.path.realpath(abs_path)
    rel_path = os.path.relpath(real_abs_path, workspace_root)

    if rel_path == '.':
        return '.'
    elif rel_path.startswith('.'):
        return rel_path
    else:
        return f"./{rel_path}"

# resolve_workplace_path: 检查路径是否合法，并转化为绝对路径
# to_workspace_display_path: 将绝对路径转换为相对路径 如： /home/dts/AI_INFRA_STUDY_MAP/month01_agent_loop/tools.py -> ./month01_agent_loop/tools.py
def is_hidden_name(name: str) -> bool:
    return name.startswith('.') or any(suffix in name for suffix in SENSITIVE_SUFFIXES)
    """
    硬规则：识别系统原生隐藏的点号前缀（.xxx）。

    软规则：强制把开发人员自定义的“高危/临时”后缀（即使系统不隐藏它）也归入隐藏类别，从而阻止用户误操作。
    """

def is_sensitive_path(path: str) -> bool:
    """
    判断路径是否属于敏感路径

    策略:
    1. 路径中包含 .env / .git / .ssh 等敏感名称
    2. 文件后缀是 .pem / .key / .crt 等密钥证书文件
    """
    normalized_path = os.path.normpath(path)
    parts = normalized_path.split(os.sep)

    for part in parts:
        if part in SENSITIVE_TOOLS:
            return True

    for suffix in SENSITIVE_SUFFIXES:
        if path.endswith(suffix):
            return True

    return False


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
    在读取之前增加敏感判断

    读取文本文件工具
    Args:
        path: 文件路径
    Returns:
        文件内容字符串
    """

    """
    读取文本工具
    只能读取 workspace 内部的普通文本文件
    """
    try:
        ok, abs_path, error = resolve_workspace_path(path)      # 所有的文件读取都先经过沙箱检查

        if not ok:
            return tool_error(f"读取失败：{error}")
        
        display_path = to_workspace_display_path(abs_path)

        if not abs_path:
            return tool_error("读取失败：path 不能为空")
        if is_sensitive_path(display_path):
            return tool_error(f"读取失败：路径是敏感路径: {display_path}")
        if not os.path.exists(abs_path):
            return tool_error(f"读取失败: 文件不存在: {display_path}")
        if os.path.isdir(abs_path):
            return tool_error(f"读取失败: 当前路径是目录，不是文件: {display_path}")
    
        with open(abs_path, "r", encoding="utf-8") as f:
            return tool_ok(str(f.read()))
    except Exception as e:
        return tool_error(f"读取失败: {e}")

def list_files(path: str, show_hidden: str="false") -> str:
    """
    列出在指定路径下的所有文件和子目录

    Args:
        path: 目录路径，默认当前路径

    Returns:
        文件和子目录列表
    """
    """
    列出指定目录下的文件和文件夹
    只能列出 workspace 内部目录
    默认隐藏隐藏文件和敏感文件
    """
    try:
        ok, abs_path, error = resolve_workspace_path(path)

        if not ok:
            return tool_error(f"列出文件失败：{error}")
        
        display_path = to_workspace_display_path(abs_path)

        show_hidden_bool = str(show_hidden).lower() == "true"
        # 将用户传入的 show_hidden 参数（无论是什么类型），统一解析并转换为一个标准的布尔值（True 或 False），用于决定是否“展示隐藏文件”

        if not os.path.exists(abs_path):
            return tool_error(f"读取失败：路径不存在 {display_path}")
        if not os.path.isdir(abs_path):
            return tool_error(f"读取失败：当前路径不是有效的目录 {display_path}")
        
        if is_sensitive_path(display_path):
            return tool_error(f"读取失败：路径是敏感路径: {display_path}")
        
        files = os.listdir(abs_path)

        if not files:
            return tool_ok(f"目录 {display_path} 是空目录")
        
        lines = [f"目录 {display_path} 下包含："]

        visible_count = 0
        hidden_mark = 0

        for file in sorted(files):
            full_path = os.path.join(abs_path, file)

            if is_sensitive_path(full_path):
                continue

            if is_hidden_name(file):
                hidden_mark += 1
                if not show_hidden_bool:
                    continue



            if os.path.isdir(full_path):
                lines.append(f"[DIR]: {file}")

            if os.path.isfile(full_path):
                lines.append(f"[FILE]: {file}")

            visible_count += 1

        if visible_count == 0:
            lines.append("没有可见文件")
        if hidden_mark > 0:
            lines.append(f"隐藏文件数量: {hidden_mark}")

        return tool_ok("\n".join(lines))        # 将一个字符串列表 lines 用换行符连接成一个大的多行字符串
    
    except Exception as e:
        return tool_error(f"读取失败：{e}")
  
def write_file(path: str, content: str) -> str:
    """
    写入文本文件工具
    Args:
        path: 文件路径
        content: 写入内容
    Returns:
        写入结果字符串
    """

    """
    写入文本文件工具
    只能写入 workspace 内部的普通文本文件
    """
    try:
        if not path:
            return tool_error("写入失败：path 不能为空")
        if not content:
            return tool_error("写入失败：content 不能为空")
        
        ok, abs_path, error = resolve_workspace_path(path)
        if not ok:
            return tool_error(f"写入失败：{error}")
        
        display_path = to_workspace_display_path(abs_path)
        # if not os.path.exists(path):
        #     return tool_error(f"写入失败，文件不存在：{path}")
        if is_sensitive_path(display_path):
            return tool_error(f"写入失败：路径是敏感路径: {display_path}")
        if os.path.isdir(abs_path):
            return tool_error(f"写入失败：当前路径是目录而不是文件: {display_path}")
        
        dir_name = os.path.dirname(abs_path)    # 如果目录不存在，他会自动创建目录

        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        
        with open(abs_path, "w") as f:
            # return tool_ok(str(f.write(content)))  # 返回写入的字符数
            f.write(content)
        return tool_ok(f"写入成功: {display_path}") 
    except Exception as e:
        return tool_error(f"写入失败: {e}")
    
TOOLS: Dict[str, Callable] = {
    "calculator": calculator,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
}

# 升级为工具注册表
TOOL_REGISTRY: Dict[str, Dict] = {
    "calculator":{
        "func": calculator,
        "description": "计算数学表达式",
        "args":{
            "expression": "数学表达式字符串，如 1 + 2 * 4"
        },
        "examples":[
            'Action: calculator(expression="1 + 2 * 4")',
            'Action: calculator(expression="2 * (3 + 4)")',
            'Action: calculator(expression="sin(pi/2))"'
        ]
    },

    "read_file":{
        "func": read_file,
        "description": "读取指定文本文件的内容",
        "args":{
            "path": "要读取的文件路径，如 ./test.txt"
        },
        "examples":[
            'Action: read_file(path="./README.md")',
            'Action: read_file(path="./test.txt")'
        ]
    },

    "write_file":{
        "func": write_file,
        "description": "向指定的文本文件中写入内容, 如果父目录不存在，会自动创建",
        "args":{
            "path": "要写入的文件路径，如 ./test.txt",
            "content": "要写入的内容，如 'Hello World!'"
        },
        "examples":[
            'Action: write_file(path="./test.txt", content="Hello World!")',
            'Action: write_file(path="/tmp/test.txt", content="Hello World!\n")'
        ]
    },

    "list_files":{
        "func": list_files,
        "description": "列出指定目录下的所有文件和子目录",
        "args":{
            "path": "要列出的目录路径，默认为当前路径",
            "show_hidden": "是否显示隐藏文件，默认为否"
        },
        "examples":[
            'Action: list_files(path="./")',
            'Action: list_files(path="./", show_hidden="true")'
        ]
    }
}

        

def get_tool_names() -> List[str]:
    """
    返回当前注册的工具名称列表
    """
    return list(TOOL_REGISTRY.keys())

def get_tool_description() -> str:
    """
    返回工具描述信息
    """
    lines = []

    for index, (tool_name, spec) in enumerate(TOOL_REGISTRY.items(), start=1):
        # TOOL_REGISTRY.items() 返回字典的（键，值）元组件
        # (tool_name, spec)：这里外层加括号是因为 enumerate 返回的是 (索引, (键, 值))，用括号直接把内部的元组解包给两个变量，写法非常 Pythonic
        # start=1：让编号从 1 开始，而非默认的 0，更符合人类阅读习惯（第1个工具、第2个工具...）

        # 函数没有直接用字符串拼接（+=），而是定义了一个 lines = []，不断 append
        lines.append(f"{index}.{tool_name}")
        lines.append(f"   功能：{spec['description']}")
        lines.append("   参数：")

        for arg_name, arg_desc in spec["args"].items():
            lines.append(f"      {arg_name}: {arg_desc}")

        lines.append("   示例：")
        for example in spec['examples']:
            lines.append(f"  - {example}")
        lines.append('')

    return "\n".join(lines)
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
    # if tool_name not in TOOLS:
    #     return tool_error(f"未知工具：{tool_name}")
    
    # tool = TOOLS[tool_name]

    if tool_name not in TOOL_REGISTRY:
        return tool_error(f"未知工具：{tool_name}")
    
    tool = TOOL_REGISTRY[tool_name]["func"]

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

    print("WORKSPACE_ROOT:", WORKSPACE_ROOT)

    print("\n正常访问:")
    print(run_tool("list_files", path="."))
    print(run_tool("read_file", path="./README.md"))
    print(run_tool("write_file", path="./tmp/sandbox_test.txt", content="hello sandbox"))
    print(run_tool("read_file", path="./tmp/sandbox_test.txt"))

    print("\n越界访问:")
    print(run_tool("list_files", path="../"))
    print(run_tool("read_file", path="../README.md"))
    print(run_tool("read_file", path="/etc/passwd"))
    print(run_tool("write_file", path="../bad.txt", content="bad"))

    print("\n敏感路径:")
    print(run_tool("read_file", path=".env"))
    print(run_tool("list_files", path=".git"))