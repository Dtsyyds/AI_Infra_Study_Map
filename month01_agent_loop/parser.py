"""
parser.py

这个文件负责解析 Agent 的输出，将 LLM 的输出解析成 Agent 可以理解的格式

第一版先实现最基础的四个解析器：
1. calculator: 计算数学表达式
2. read_file: 读取文本文件
3. write_file: 写入文本文件
4. Finish: 结束

注意：
- 解析器函数统一返回字符串，方便后续作为 Obersvation 交给 LLM

"""
import re

cal_text = f"Action: calculator(expression=\"1 + 2 * 4\")"
read_text = f"Action: read_file(path=\"./README.md\")"
write_text = f"Action: write_file(path=\"./test.txt\", content=\"hello world!\")"
finish_text = f"Action: Finish[任务完成]"

def parse_tools(input: str) -> str:
    if not input.startswith("Action:"):
        return f"Action判断不是有效的调用工具指令"

    pattern_text = r"Action:\s*(\w+)\((.*)\)"
    # Action  匹配固定字符串 Action:
    # \s* 匹配任意空格
    # (\w+) 匹配一个或多个单词字符 这里匹配工具名
    # \( 匹配左括号
    # (.*) 匹配括号里面的参数
    # \) 匹配右括号
    match = re.search(pattern_text, input)

    if not match:
        return f"不是有效的调用工具指令"
    tool_name = match.group(1)
    args_text = match.group(2)
    print(f"调用的工具是：{tool_name} 传入的参数是：{args_text}")

    # 解析参数]
    
    args_pair = re.findall(r'(\w+)="([^"]*)"', args_text)
    # (\w+) 匹配参数名，比如 expression
    # = 匹配等号
    # " 匹配双引号
    # ([^"]*) 匹配双引号里面的内容
    # " 匹配结束双引号

    args = dict(args_pair)

    return {
        "type": "tools",
        "tool_name": tool_name,
        "args": args
    }

def parse_Finish(input: str) -> str:
    if not input.startswith("Action:"):
        return f"Action判断不是有效的调用工具指令"
    pattern_text = r"Action:\s*(\w+)\[(.*)\]"
    # Action  匹配固定字符串 Action:
    # \s* 匹配任意空格
    # (\w+) 匹配一个或多个单词字符 这里匹配工具名
    # \( 匹配左括号
    # (.*) 匹配括号里面的参数
    # \) 匹配右括号
    match = re.search(pattern_text, input)
    if not match:
        return f"不是有效的结束指令"
    Finish_text = match.group(1)
    args_text = match.group(2)

    return {
        "type": Finish_text,
        "args": args_text,
    }

def parse_action(input: str) -> dict:
    # if not input.startswith("Action:"):
    #     return f"Action判断不是有效的调用工具指令"
    # # 去掉前面的 action
    # action_text =  input[len("Action:"):].strip()

    # # 情况1 Finish
    # finish_match = re.search(r"Finish\[(.*)\]", action_text)
    # # print(finish_match)
    # if finish_match:
    #     return {
    #         "type": "finish",
    #         "content": finish_match.group(1).strip()
    #     }
    
    # # 情况2 其他工具
    # tool_match = re.search(r"(\w+)\((.*)\)", action_text)
    # if not tool_match:
    #     return {
    #         "type": "error",
    #         "content": "不是有效的工具调用指令"
    #     }
    # tool_name = tool_match.group(1)
    # args_text = tool_match.group(2)

    # # 解析表达式中的参数
    # args_pair = re.findall(r'(\w+)="([^"]*)"', args_text)
    # args = dict(args_pair)

    # return {
    #     "type": "tool",
    #     "tool_name": tool_name,
    #     "args": args
    # }

    """
    解析 Action 指令

    支持：
    Action: calculator(expression="1 + 2 * 4")
    Action：calculator(expression="1 + 2 * 4")
    Action: Finish[最终答案]
    Action：Finish[最终答案]
    """
    text = input.strip()

    # 统一中文冒号
    text = text.replace("：", ":")

    if not text.startswith("Action:"):
        return {
            "type": "error",
            "content": f"不是有效的 Action 指令"
        }
    
    # 去掉前面的 action
    action_text = text[len("Action:"):].strip()

    # 情况 1：Finish
    pattern = r"Finish\[(.*)\]"
    finish_match = re.search(pattern, action_text)

    if finish_match:
        return {
            "type": "finish",
            "content": finish_match.group(1).strip()
        }
    
    # 情况 2： 其他工具
    pattern = r"(\w+)\((.*)\)"
    tool_match = re.search(pattern, action_text)

    if not tool_match:
        return {
            "type": "error",
            "content": "不是有效的工具调用指令"
        }
    tool_name = tool_match.group(1)
    args_text = tool_match.group(2)

    # 解析表达式中的参数
    # args_pair = re.find(r"(\w+)=\"(.*)\"", args_text)
    # 输入：name="John" age="25" 输出：[('name', 'John" age="25')]  ❌ 全吞了！ 因为 .* 贪婪匹配，会从 John 后面的引号一直吃到最后一个引号（即 25 后面的引号），把所有中间内容都当成了 group(2)
    args_pair = re.findall(r'(\w+)="([^"]*)"', args_text)
    args = dict(args_pair)

    return {
        "type": "tool",
        "tool_name": tool_name,
        "args": args
    }
    """
    (\w+)    匹配一个或多个单词字符，这里的参数名 expression
    =        匹配等号
    "        匹配左双引号
    ([^"]*)  匹配双引号里面的内容   [^"]    匹配任意一个不是双引号的字符 ^取反  [^"]* 连续匹配任意多个非双引号字符
    "        匹配右双引号
    """
if __name__ == "__main__":
    # print(parse_action(cal_text))
    # print(parse_action(read_text))
    # print(parse_action(write_text))
    # print(parse_action(finish_text))

    test_cases = [
        'Action: calculator(expression="1 + 2 * 4")',
        'Action：calculator(expression="1 + 2 * 4")',
        'Action: read_file(path="./test.txt")',
        'Action：write_file(path="./test.txt", content="hello world")',
        'Action: Finish[任务完成]',
        'Action：Finish[任务完成]',
        'hello world',
        'Action: wrong format',
    ]

    for text in test_cases:
        print(f"测试用例：{text}")
        result = parse_action(text)
        print("解析结果:", result)


