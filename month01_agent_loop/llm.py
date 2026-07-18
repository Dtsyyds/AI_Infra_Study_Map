"""
llm.py

这个文件负责封装 LLM 的调用

当前阶段先实现 FakerLLM
- 不调用真实的大模型 API
- 根据用户输入模拟大模型的输出生成  Thought / Action 格式的输出
- 用来测试 Agent Loop 的整体链路
"""
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

from tools import is_sensitive_path

load_dotenv()

class FakerLLM:

    def generate(self, prompt: str) -> str:
        """
        根据 prompt 模拟生成 Thought / Action 格式的输出
        """
        user_input = self._extract_user_input(prompt)
        print(f"FakerLLM 收到用户输入: {user_input}")

        last_observation = self._extract_last_observation(prompt)
        if last_observation:
            if last_observation.startswith("TOOL_OK:"):
                content = last_observation.removeprefix(
                    "TOOL_OK:"
                ).strip()

                if self._is_list_files_task(user_input):
                    return (
                        "Thought: 工具已经成功执行，我需要整理成自然语言回答\n"
                        f'Action: Finish[当前目录包含以下文件和文件夹：{content}]'
                    )
                return (
                    "Thought: 工具已经成功执行，我应该整理成自然语言回答\n"
                    f"Action: Finish[工具执行结果是：{content}]"
                )

            if last_observation.startswith("TOOL_ERROR:"):
                error = last_observation.removeprefix(
                    "TOOL_ERROR:"
                ).strip()

                return (
                    "Thought: 工具执行失败，我应该解释原因并给出检查建议\n"
                    f"Action: Finish[工具执行失败，请检查：{error}]"
                )

            # 兼容没有 TOOL_OK/TOOL_ERROR 前缀的 Observation
            return (
                "Thought: 我已经拿到了工具返回结果，可以给出最终答案\n"
                f"Action: Finish[工具执行结果是：{last_observation}]"
            )
        
        if self._is_calculate_task(user_input):
            expression = self._extract_expression(user_input)

            return (
                "Thought: 用户需要计算数学表达式, 我应该调用 calculator 工具\n"
                f'Action: calculator(expression="{expression}")'
            )
        
        if self._is_list_files_task(user_input):
            return (
                "Thought: 用户需要查看当前目录，我应该调用 list_files 工具\n"
                'Action: list_files(path=".")' 
            )
        
        if user_input.startswith("读取"):
            path = user_input.replace("读取", "", 1).strip()

            # 去掉一些自然语言后缀
            for suffix in ["文件内容", "的内容", "内容", "文件"]:
                path = path.replace(suffix, "").strip()

            # 增加第一层防护：敏感文件在调用前拒绝
            if is_sensitive_path(path):
                return (
                    "Thought: 用户需要读取文件内容, 但该文件敏感，拒绝执行\n"
                    f'Action: Finish[出于安全考虑，{path} 属于敏感路径，不能读取]'
                )
            
            # 项目目录外路径在工具调用前拒绝
            if self._is_workspace_boundary_path(path):
                return (
                    "Thought: 用户需要读取文件内容, 但该路径不属于项目目录，拒绝执行\n"
                    "Action: Finish[无法读取：该路径超出项目目录的访问范围]"
                )
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

        # 解决问题1：prompts.py 生成用户输入时用的是中文冒号，这里替换为英文
        # prompt = prompt.replace("：", ":")

        # match = re.search(r"用户输入:\n(.*)", prompt);
        match = re.search(
            r"用户输入[:：]\s*\n(.*?)(?=\n\n请输出 Thought 和 Action|\Z)",
            prompt,
            flags=re.DOTALL
        )
        """
        从 prompt 字符串中提取紧跟在“用户输入:”或“用户输入：”之后（允许冒号后有空白和换行）的一大段内容，直到遇到下一个 \n\n请输出 Thought 和 Action 或字符串结尾为止。
        """
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

        """
        提取最后一次完整的 Observation。

        支持：
        - 单行 Observation
        - 多行文件列表
        - 多轮 Observation
        """

        pattern = (
            r"^observation:\s*(.*?)"
            r"(?="
            r"^(?:user|assistant|observation|system):"
            r"|^用户输入[：:]"
            r"|\Z"
            r")"
        )

        matches = re.findall(
            pattern,
            prompt,
            flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )

        if not matches:
            return ""

        return matches[-1].strip()
        # matches = re.findall(r"observation:\s*(.*)", prompt)
        # if not matches:
        #     matches = re.findall(r"Observation:\s*(.*)", prompt)
        # if matches:
        #     return matches[-1].strip()
        # else:
        #     return ""
            
    
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
    
    def _is_list_files_task(self, user_input: str) -> bool:
        """
        判断用户输入是否是查看当前目录的任务
        """
        keywords = [
            "查看当前目录", "列出文件", "显示所有文件和文件夹",
            "列出当前目录下的内容", "展示当前目录的文件列表", 
            "请列出当前目录中的所有项目或文件"
        ]
        return any(
            keyword in user_input for keyword in keywords
        )
    
    def _is_workspace_boundary_path(self, path: str) -> bool:
        normalized_path = os.path.normpath(path)

        return (
            os.path.isabs(path)
            or path.startswith("~")
            or normalized_path == ".."
            or normalized_path.startswith(".." + os.sep)
        )

class OpenAICompatibleLLM:
    """
    真实 OpenAI-compatible LLM 调用

    当前支持从 .env 读取：

    Agent AI 风格：
    - Agent_AI_API_KEY
    - Agent_BASE_URL
    - Agent_MODEL_NAME

    同时兼容 OpenAI 风格：
    - OPENAI_API_KEY
    - OPENAI_BASE_URL
    - MODEL_NAME
    """

    def __init__(self):
        api_key = (
            os.getenv("Agent_AI_API_KEY") or os.getenv("OPENAI_API_KEY")
        )

        base_url = (
            os.getenv("Agent_AI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        )

        model_name = (
            os.getenv("Agent_AI_MODEL_NAME") or os.getenv("MODEL_NAME")
        )

        if not api_key:
            raise ValueError("Missing API key")

        if not base_url:
            raise ValueError("Missing Base URL")

        if not model_name:
            raise ValueError("Missing Model Name")

        self.model_name = model_name
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def generate(self, prompt: str) -> str:
        """
        调用真实的 LLM, 返回 Thought / Action 格式的输出
        """

        """
        V7.3 调用真实 LLM, 返回 Thought / Action 文本
        增加简单的重试机制：
        - 如果第一次 API 调用失败，自动再试一次
        - 如果两次都失败，抛出 RuntimeError, 由 agent.py 捕捉
        """

        last_error = None

        for attempt in range(2):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "system",
                            "content":(
                                "你是一个严格的 Agent 动作生成器"
                                "你只能输出 Thought / Action 两行"
                                "不要输出 Markdown"
                                "不要输出代码块"
                                "不要输出额外解释"
                            ),
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0,
                    timeout=30,
                )
                
                content = completion.choices[0].message.content

                if content is None:
                    return "Thought: 模型没有返回内容\nAction: Finish[模型没有返回内容]"
                
                return self._clean_output(content)
            
            except Exception as e:
                last_error = e
                print(f"[LLM Error] 第 {attempt + 1} 次调用失败：{e}")

        raise RuntimeError(f"真实 LLM 调用失败，已重试两次，最后一次错误：{last_error}")
    
    def _clean_output(self, text: str) -> str:
        """
        清理模型可能输出的 Markdown 代码块
        """

        text = text.strip()

        if text.startswith("```"):
            lines = text.split("\n")
            code_lines = []
            for line in lines:
                if line.startswith("```"):
                    break
                elif line.startswith("#"):
                    continue
                else:
                    code_lines.append(line)
            return "\n".join(code_lines)
        else:
            return text
        
def call_llm(prompt: str) -> str:
    """
    统一的 LLM 调用接口
    
    LLM_MODE=fake 使用 FakerLLM
    LLM_MODE=real 使用真实 API
    
    """

    # llm = FakerLLM()
    mode = os.getenv("LLM_MODE", "fake").lower()

    if mode == "real":
        llm = OpenAICompatibleLLM()
    else:
        llm = FakerLLM()

    return llm.generate(prompt)

if __name__ == "__main__":
    from prompts import build_prompt
    # print("测试 FakerLLM")

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
