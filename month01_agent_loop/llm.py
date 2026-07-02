"""
llm.py

这个文件负责封装 LLM 的调用

当前阶段先实现 FakerLLM
- 不调用真实的大模型 API
- 根据用户输入模拟大模型的输出生成  Thought / Action 格式的输出
- 用来测试 Agent Loop 的整体链路
"""

import re

class FakerLLM:

    def generate(self, prompt: str) -> str:
        """
        根据 prompt 模拟生成 Thought / Action 格式的输出
        """
        user_input = self._extract_user_input(prompt)
        print(f"FakerLLM 收到用户输入: {user_input}")

        last_observation = self._extract_last_observation(prompt)
        if last_observation:
            return (
                "Thought: 我已经拿到了工具返回的观察结果，可以基于它给出最终答案\n"
                f'Action: Finish[{last_observation}]'
            )
        
        if self._is_calculate_task(user_input):
            expression = self._extract_expression(user_input)

            return (
                "Thought: 用户需要计算数学表达式, 我应该调用 calculator 工具\n"
                f'Action: calculator(expression="{expression}")'
            )
        
        if user_input.startswith("读取"):
            path = user_input.replace("读取", "", 1).strip()

            # 去掉一些自然语言后缀
            for suffix in ["文件内容", "的内容", "内容", "文件"]:
                path = path.replace(suffix, "").strip()

            return (
                "Thought: 用户需要读取文件内容, 我应该调用 read_file 工具\n"
                f'Action: read_file(path="{path}")'
            )
        
        if user_input.startswith("写入"):
            rest =  user_input.replace("写入","", 1).strip()
            path, content = rest.split(" ", 1)

            return (
                "Thought: 用户需要写入文件内容, 我应该调用 write_file 工具\n"
                f'Action: write_file(path="{path}", content="{content}")'
            )
        
        return (
            "Thought: 当前任务不需要调用工具，或者 FakerLLM 暂时无法识别用户意图\n"
            "Action: Finish[当前 FakerLLM 只能处理计算、读取和写入任务]"
        )
    
    def _extract_user_input(self, prompt: str) -> str:
        """
        从完整 prompt 中提取用户输入
        
        Args:
            prompt: 完整的 prompt 字符串
        Returns:
            用户输入的内容
        """

        match = re.search(r"用户输入:\n(.*)", prompt);
        if match:
            return match.group(1)
        return ""
    
    def _extract_last_observation(self, prompt: str) -> str:
        """
        从完整 Prompt 中提取最后一次的 observation
        
        memory_content 中可能包含多次 observation, 这里只取最后一次
        正常返回 observation: 9

        Args:
            prompt: 完整的 prompt 字符串
        Returns:
            最后一次的 observation
        """
        matches = re.findall(r"observation:\s*(.*)", prompt)
        if not matches:
            matches = re.findall(r"Observation:\s*(.*)", prompt)
        if matches:
            return matches[-1].strip()
        else:
            return ""
            
    
    def _is_calculate_task(self, user_input: str) -> bool:
        """
        判断用户输入是否是计算任务
        
        Args:
            user_input: 用户输入的内容
        Returns:
            是否是计算任务
        """
        calculate_keywords = ["计算", "求值", "evaluate", "calculate", "算一下", "算出", "算"]

        return any(keyword in user_input for keyword in calculate_keywords)
    
    def _extract_expression(self, user_input: str) -> str:
        """
        从用户输入中提取数学表达式
        Args:
            user_input: 用户输入的内容
        Returns:
            数学表达式
        """

        # 优先匹配连续的数字和运算符
        match = re.search(r"([0-9+\-*/().\s]+)", user_input)
        if match:
            return match.group(0).strip()
        
        return user_input.replace("计算", "", 1).strip()
    
def call_llm(prompt: str) -> str:
    """
    统一的 LLM 调用接口
    
    现在使用 FakerLLM
    后续只需要修改这里即可替换为其他 LLM 实现
    """

    llm = FakerLLM()

    return llm.generate(prompt)

if __name__ == "__main__":
    from prompts import build_prompt
    print("测试 FakerLLM")

    test_cases = [
        "帮我计算 1 + 2 * 4 的结果",
        "请帮我计算 (3 + 5) * 2",
        "读取 month01_agent_loop/prompts.py 文件内容",
        "写入 month01_agent_loop/test.txt Hello World",
        "你好"
    ]

    for user_input in test_cases:
        print(f"用户输入: {user_input}")
        prompt = build_prompt(user_input, "")
        response = call_llm(prompt)
        print(response)
