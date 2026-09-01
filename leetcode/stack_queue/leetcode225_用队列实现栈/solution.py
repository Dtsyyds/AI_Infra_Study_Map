from collections import deque

class MyStack:
    def __init__(self):
        self.deque1 = deque()
        self.deque2 = deque()

    def push(self, x: int) -> None:
        # 将新元素放入空队列 deque2
        self.deque2.append(x)
        # 将 deque1 中所有元素转移到 deque2（这样老元素就在新元素后面）
        while self.deque1:
            self.deque2.append(self.deque1.popleft())
        # 交换两个队列，使 deque2 永远保持“栈”的顺序
        self.deque1, self.deque2 = self.deque2, self.deque1

    def pop(self) -> int:
        return self.deque1.popleft()   # 注意：现在主队列是 deque1

    def top(self) -> int:
        return self.deque1[0]

    def empty(self) -> bool:
        return not self.deque1