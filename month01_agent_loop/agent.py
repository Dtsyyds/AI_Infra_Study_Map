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
import re
from trace import AgentTrace

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
        # Agent trace 记录
        trace = AgentTrace()
        # 当前任务专用 scratchpad
        task_memory = Memory(max_messages=20)
        action_history = []
        last_observation = ""
        # 1. 记录用户输入
        self.memory.add_user_message(user_input)
        # 同步记录到 track 中
        trace.set_user_input(user_input)

        for step in range(self.max_steps):
            print(f"======== step: {step} ======")
            # memory_content = self.memory.get_content()
            memory_content = task_memory.get_content()
            prompt = build_prompt(user_input, memory_content)
            
            # 适配 7.3 修改
            try:
                llm_output = call_llm(prompt)
            except Exception as e:
                error_msg = f"LLM 调用失败， 错误原因：{e}"

                result = {
                    "type": "llm_error",
                    "content": error_msg,
                    "success": False,
                    }
                
                trace.add_step(
                    step_index=step,
                    llm_output="",
                    action="",
                    result=result
                )

                final_answer = (
                    "LLM 调用失败，Agent 已停止执行本次任务。\n"
                    f"错误原因：{e}"
                )

                trace.finish(final_answer, status="llm_error")
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                return final_answer

            # 模型 API 挂了之后，不日再让 Python 程序直接 traceback, 而是把错误包装成一次 Agent 失败结果。
            print("\n[LLM Output]")
            print(llm_output)

            task_memory.add_ai_message(llm_output)
            self.memory.add_ai_message(llm_output)

            action =  self._extract_action_line(llm_output)
            print("\n[Agent Action]")
            print(action)
            # # 同一个任务里，如果模型第二次调用完全相同的 Action，就停止执行
            # if action in action_history:
            #     final_answer = (
            #         "模型重复调用了相同的 Action, Agent 已停止执行，避免死循环。\n"
            #         f"重复 Action: {action}"
            #     )
            
            #     self.memory.add_ai_message(f"Final Answer: {final_answer}")
            #     return final_answer
            
            # 如果上一次工具已经成功，但模型重复调用同一个 Action, 就让 Agent 直接基于上一次 observation 返回一个更自然的失败兜底
            if action in action_history:
                if last_observation.startswith("TOOL_OK:"):
                    final_answer = (
                        "工具已经成功返回结果，但模型重复调用了相同的 Action。"
                        "请根据上一次工具结果重新提问，或者看调试输出中的 Observation。"
                    )
                else:
                    final_answer = (
                        "模型重复调用了相同的 Action, Agent 已停止执行，避免死循环。"
                        f"重复 Action: {action}"
                    )
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                trace.finish(final_answer, status="stopped")
                return final_answer
            
            action_history.append(action)
            result = execute_action(action)
            print("\n[RESULT]")
            print(result)

            trace.add_step(
                step_index=step,
                llm_output=llm_output,
                action=action,
                result=result
            )

            if result["type"] == "observation":
                observation = result["content"]
                last_observation = observation
                task_memory.add_observation(observation)
                self.memory.add_observation(observation)

                continue

            if result["type"] == "finish":
                final_answer = result["content"]
                task_memory.add_ai_message(f"Final Answer: {final_answer}")
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                trace.finish(final_answer, status="success")
                return final_answer
            
            if result["type"] == "error":
                error_msg = result["content"]
                task_memory.add_observation(f"Error: {error_msg}")
                self.memory.add_observation(f"Error: {error_msg}")
                final_answer = f"执行失败，错误原因：{error_msg}"
                task_memory.add_ai_message(f"Final Answer: {final_answer}")
                self.memory.add_ai_message(f"Final Answer: {final_answer}")
                trace.fail(error_msg, status="error")
                return final_answer
        
        final_answer = "任务超过最大执行步骤，无法继续执行。"
        # task_memory.add_ai_message(f"Final Answer: {final_answer}")
        self.memory.add_ai_message(f"Final Answer: {final_answer}")
        trace.finish(final_answer, status="max_steps_exceeded") 

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

        # for line in llm_output.splitlines():
        #     line = line.strip()

        #     if line.startswith("Action:"):
        #         return line
            
        # return "Action: Finish[LLM 输出中没有 Action 行]"

        """
        从 LLM 输出中提取 Action 行

        支持：
        1. Action: calculator()
        2. Action：calculator()
        3. Markdown 代码在块中的 Action
        4. 前面带多余解释的输出

        由于 LLM 输出的不确定性，真实模型有时会输出多行 Action, 有时会输出单行，现重新优化代码：
        支持：
        1. 单行工具调用：
            Action: calculator(expression="1 + 2 * 4")
        2. 单行 Finish
            Action: Finish[结果]
        3. 多行 Finish:
            Action: Finish[第一行
            第二行
            第三行]
        4. 中文冒号
            Action：calculator(expression="1 + 2 * 4")
        """
        text = llm_output.strip()

        # 去掉 Markdown 代码块标记
        text = text.replace("```text", "")
        text = text.replace("```python", "")
        text = text.replace("```", "")

        # 统一中文冒号
        text = text.replace("Action：", "Action:")

        # 优先匹配多行 Finish
        finish_match = re.search(
            r"Action:\s*Finish\[(.*)\]\s*$",    # $ 匹配字符串的结尾，意味着 Finish[...]必须是整段文本的最后一部分，后面不能再有其他字符
            text,
            flags=re.DOTALL     # 默认情况下正则匹配里面 . 不匹配换行符，加上这个标志，就可以匹配包括换行符在内的任意字符
        )

        if finish_match:
            content = finish_match.group(1).strip()
            return f"Action: Finish[{content}]"
        
        # 再匹配工具调用，一般工具调用都是一行
        pattern =  r"Action:\s*(\w+\(.*?\))"
        tool_match = re.search(
            pattern,
            text,
            flags=re.DOTALL
        )
        if tool_match:
            return f"Action: " + tool_match.group(1).strip()

        return "Action: Finish[模型没有输出 Action]"
        # # 第一优先级，逐行查找 Action
        # for line in text.splitlines():
        #     line = line.strip()

        #     if line.startswith("Action:"):
        #         return line
            
        # # 第二优先级：整段文本中正则匹配 Action:
        # pattern = r"Action\s*[:]\s*(.+)"       # r:表示原始字符串，不转义 Action：字面匹配字符 Action \s* 匹配0个或多个空白字符 [:] 匹配一个冒号 \s* 再次匹配0个或多个空白字符 (.+) 匹配一个或多个任意字符（除换行符\n）
        # """
        # 匹配项：

        # Action: Start 匹配      Action : Run 匹配，空格任意     Action:: test 不匹配，只允许一个冒号       Action:  不匹配，冒号后面至少需要一个字符
        # """
        # match = re.search(pattern, text)

        # if match:
        #     return "Action: " + match.group(1).strip()
        
        # return "Action: Finish[模型没有输出 Action]"
                
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



    



    