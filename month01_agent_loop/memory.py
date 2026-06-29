"""
memory.py

这个文件负责管理 Agent 的短期记忆

当前只实现最基本最基础的该功能：
1. 保存用户输入
2. 保存 Agent 输出
3. 保存工具 Observation
4. 生成上下文字符串
5. 支持清空记忆
6. 限制最大消息数量，防止上下文过长

注意：
- 当前 Memory 使用 List 存储
- 后续可以升级为摘要记忆、长期记忆、向量数据库记忆
"""

from typing import List, Dict

class Memory:
    """
    最小短期记忆模块

    性天每条消息都用 dict 表示：
    {
        "role": "user" /  "assistant" / "observation" / "system",
        "content": "消息内容"
    }
    """

    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        self.messages: List[Dict[str, str]] = []

    def add_message(self, role: str, content: str) -> dict:
        if len(self.messages) >= self.max_messages:
            del self.messages[0]
        message = {"role": role, "content": content}
        self.messages.append(message)
        print(f"{role}: {content}添加成功")
        return self.messages[-1]

    def add_user_message(self, content: str) -> dict:
        return self.add_message("user", content)
    
    def add_ai_message(self, content: str) -> dict:
        return self.add_message("assistant", content)
    
    def add_observation(self, content: str) -> dict:
        return self.add_message("observation", content)
    
    def add_system_message(self, content: str) -> dict:
        return self.add_message("system", content)
    
    def get_message_special(self, index: int) -> dict:
        return self.messages[index]
    
    def get_message(self) -> List[Dict[str, str]]:
        """ 获取原始消息列表 """
        return self.messages
    
    def get_context(self) -> str:
        """
        将消息列表转换成适合放进 Prompt 的上下文字符串
        """
        lines = []

        for msg in self.messages:
            role = msg["role"]
            content = msg["content"]

            if role == 'user':
                lines.append(f"user: {content}")
            elif role == 'assistant':
                lines.append(f"assistant: {content}")
            elif role == 'observation':
                lines.append(f"observation: {content}")
            elif role == 'system':
                lines.append(f"system: {content}")
            else:
                lines.append(f"role: {content}")
        return "\n".join(lines)     # 将列表中的所有字符串用换行符 \n 连接成一个完整的大字符串

    def clear(self):
        self.messages.clear()

if __name__ == "__main__":
    memory = Memory(max_messages=10)

    memory.add_user_message("帮我计算 1 + 2 * 4")
    memory.add_ai_message("Thought: 我需要调用计算器工具。")
    memory.add_observation("计算结果是 9")
    memory.add_ai_message("Final Answer: 结果是9")

    print("原始消息列表")
    print(memory.get_message())
    print("\n上下文字符串")
    print(memory.get_context())
    print("清空记忆")
    memory.clear()
    print(memory.get_message())


    

