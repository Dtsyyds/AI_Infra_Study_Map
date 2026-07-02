"""
agent.py

V0 版本 Agent

第一版不介入真实的 LLM ,而是用简单规则模拟 LLM 的决策

目标：
1. 接收用户输入
2. 根据规则生成 Thought 和 Action
3. 调用 executor.py 执行 Action
4. 使用 memory.py 记录 user / Thought / Action / observation
5. 返回最终结果

当前功能：
- 计算： 计算 1 + 2 * 4
- 读取： 读取 ./text.txt
- 写入： 写入 ./text.txt hello agent
"""

from memory import Memory
from executor import execute_action
from memory import Memory
from prompts import build_prompt
from llm import *

class RuleBaseAgent:
    """
    V0 规则版 Agent
    这个暂时不用 LLM, 而是用 if/else 规则生成 Action
    TODO: 后续可以考虑用 LLM 来生成规则
    """
    def __init__(self, max_steps: int = 3):
        self.memory = Memory(max_steps)
        self.max_steps = max_steps

    def generate_action(self, user_input: str) -> tuple[str, str]:
        """
        根据用户输入生成 Thought 和 Action

        Args:
            user_input: 用户输入

        Returns:
            thought: 思考过程
            action: 行动
        """
        user_input = user_input.strip()     # 去掉输入字符串首位的所有空白字符（空格、制表符、换行符等）

        # 1. 计算
        if user_input.startswith('计算'):
            expression = user_input.replace("计算", "", 1).strip()      # 将 "计算" 替换为空字符串，并去掉首尾空白字符
            thought = f"Thought: 用户需要计算数学表达式，我应该调用 calculator 工具。"
            action = f'Action: calculator(expression="{expression}")'
            return thought, action
        
        # 2. 读取文件
        if user_input.startswith("读取"):
            expression = user_input.replace("读取", "", 1).strip()
            thought = f"Thought: 用户需要读取文件内容，我应该调用 reader 工具。"
            action = f'Action: read_file(path="{expression}")'
            return thought, action
        
        # 3. 写入文件
        if user_input.startswith("写入"):
            expression = user_input.replace("写入", "", 1).strip()
            parts = expression.split(maxsplit=1)
            if len(parts) < 2:
                thought = f"Thought: 用户输入了无效的命令，我应该告诉他怎么做。"
                action = f'Action: say("请输入有效的命令")'
                return thought, action
            path = parts[0]
            content = parts[1]
            thought = f"Thought: 用户需要写入文件内容，我应该调用 writer 工具。"
            action = f'Action: write_file(path="{path}", content="{content}")'
            return thought, action
        
        # 4. 其他情况
        else:
            thought = f"Thought: 用户输入了无效的命令，我应该告诉他怎么做。"
            action = f'Action: say("请输入有效的命令")'
            return thought, action
        
    def run(self, user_input: str) -> str:
        """
        执行一次 Agent 流程

        Args:
            user_input: 用户输入
        Returns:
            最终回答字符串
        """

        self.memory.add_user_message(user_input)
        
        for step in range(self.max_steps):
            thought, action = self.generate_action(user_input)
            self.memory.add_ai_message(thought)
            self.memory.add_ai_message(action)
            print("\n[Agent Thought]")
            print(thought)
            print("\n[Agent Action]")
            print(action)

            result = execute_action(action)
            if result["type"] == "observation":
                observation = result["content"]
                self.memory.add_observation(observation)
                print("\n[Observation]")
                print(observation)

                final_answer = f"执行完成，工具返回结果：{observation}"
                self.memory.add_ai_message(f"Final Answer: {final_answer}")

                return final_answer
            
            if result["type"] == "finish":
                final_answer = result["content"]
                self.memory.add_ai_message(f"Final Answer: {final_answer}")

                return final_answer
            
            if result["type"] == "error":
                error_msg = result["content"]
                self.memory.add_observation(f"Error: {error_msg}")

                final_answer = f"执行失败，错误原因：{error_msg}"
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                return final_answer
            
        final_answer  = "任务超过最大执行步骤，无法继续执行。"
        self.memory.add_ai_message(f"Final Answer: {final_answer}")
        return final_answer
    
    def show_memory(self) -> str:
        """
        查看当前 Agent 的记忆上下文
        """
        return self.memory.get_context()
    
    def clear_memory(self) -> None:
        """
        清除当前 Agent 的记忆上下文
        """
        self.memory.clear()

class LLMAgent:
    """
    V1 FakerLLM Agent
    
    这个版本不再由 agent.py 直接生成 Thought / Action, 而是通过 prompts.py 构建 prompt ，再调用 LLM 来生成 Thought / Action
    """

    def __init__(self, max_steps: int = 3):
        self.memory = Memory(max_messages=50)
        self.max_steps = max_steps

    def run(self, user_input: str) -> str:
        """
        执行最大次数下的 Agent 流程
        """

        # 当前任务专用 scratchpad
        task_memory = Memory(max_messages=20)
        # 1. 记录用户输入
        self.memory.add_user_message(user_input)

        for step in range(self.max_steps):
            print(f"======== step: {step} ======")
            # memory_content = self.memory.get_content()
            memory_content = task_memory.get_content()
            prompt = build_prompt(user_input, memory_content)
            
            llm_output = call_llm(prompt)

            task_memory.add_ai_message(llm_output)
            self.memory.add_ai_message(llm_output)

            action =  self._extract_action_line(llm_output)

            result = execute_action(action)

            if result["type"] == "observation":
                observation = result["content"]
                task_memory.add_observation(observation)
                self.memory.add_observation(observation)

                continue

            if result["type"] == "finish":
                final_answer = result["content"]
                task_memory.add_ai_message(f"Final Answer: {final_answer}")
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                return final_answer
            
            if result["type"] == "error":
                error_msg = result["content"]
                task_memory.add_observation(f"Error: {error_msg}")
                self.memory.add_observation(f"Error: {error_msg}")
                final_answer = f"执行失败，错误原因：{error_msg}"
                task_memory.add_ai_message(f"Final Answer: {final_answer}")
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                return final_answer
        
        final_answer = "任务超过最大执行步骤，无法继续执行。"
        # task_memory.add_ai_message(f"Final Answer: {final_answer}")
        self.memory.add_ai_message(f"Final Answer: {final_answer}")

        return final_answer
        # # 2. 从 memory 中获取历史上下文
        # memory_content = self.memory.get_content()
        # # 3. 构建 prompt
        # prompt = build_prompt(user_input, memory_content)
        # print("\n[Prompt]")
        # print(prompt)

        # # 4. 调用 LLM 生成 Thought / Action
        # llm_output = call_llm(prompt)
        # print("\n[LLM Output]")
        # print(llm_output)

        # # 5. 记录 LLM 输出
        # self.memory.add_ai_message(llm_output)

        # # 6. 从 LLM 中解析 Action
        # action = self._extract_action_line(llm_output)

        # # 7. 执行 Action
        # result = execute_action(action)

        # print(result)

        # # 8. 根据执行的结果返回最终答案
        # if result["type"] == "observation":
        #     observation = result["content"]
        #     self.memory.add_observation(observation)
        #     final_answer = f"执行完成，工具返回结果：{observation}"
        #     self.memory.add_ai_message(f"Final Answer: {final_answer}")
        #     return final_answer
        
        # if result["type"] == "finish":
        #     final_answer = result["content"]
        #     self.memory.add_ai_message(f"Final Answer: {final_answer}")
        #     return final_answer
        # if result["type"] == "error":
        #     error_msg = result["content"]
        #     self.memory.add_observation(f"Error: {error_msg}")
        #     final_answer = f"执行失败，错误原因：{error_msg}"
        #     self.memory.add_ai_message(f"Final Answer: {final_answer}")
        #     return final_answer
        
        # final_answer = "未知错误"
        # self.memory.add_ai_message(f"Final Answer: {final_answer}")
        # return final_answer
    
    def _extract_action_line(self, llm_output: str) -> str:
        """
        从 LLM 输出中提取 Action 行
        
        LLM 输出格式：

        Thought: 用户需要计算的数学表达式
        Action: calculator(expression="1 + 2 * 4")

        executor.py 只需要 Action 行
        """

        for line in llm_output.splitlines():
            line = line.strip()

            if line.startswith("Action:"):
                return line
            
        return "Action: Finish[LLM 输出中没有 Action 行]"
    
    def show_memory(self) -> str:
        return self.memory.get_content()
    
    def clear_memory(self) -> None:
        self.memory.clear()

if __name__ == "__main__":
    # agent = RuleBaseAgent(max_steps=3)
    agent = LLMAgent(max_steps=3)

    test_cases = [
        "计算 1 + 2 * 4",
        "写入 ./test.text hello world",
        "读取 ./test.text",
        "hello"
    ]
    for user_input in test_cases:
        answer = agent.run(user_input)
        print(f"\nUser Input: {user_input}\nAnswer: {answer}\n")
        print(agent.show_memory())
        agent.clear_memory()



    



    