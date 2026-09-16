from collections import deque
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class InferenceRequest:
    request_id: str
    prompt_tokens: int
    max_new_tokens: int
    arrived_at: float

class RequestQueueFullError(RuntimeError):
    """等待队列容量已满"""

class RequestQueueEmptyError(RuntimeError):
    """等待队列中没有请求。"""

class RequestQueue:
    def __init__(self, *, capacity: int):
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity 必须是大于等于 1 的整数")

        self._capacity = capacity
        # self._queue = deque()
        self._queue: deque[InferenceRequest] = deque()

    def submit(self, request: InferenceRequest) -> None:
        # 因为实现了功能函数 len(self) == len(self._queue)
        if len(self) >= self._capacity:
            raise RequestQueueFullError("队列已满")

        self._queue.append(request)

    def pop_next(self) -> InferenceRequest:     # 取出并删除
        if not self._queue:
            raise RequestQueueEmptyError("队列为空")

        return self._queue.popleft()

    # 调度器必须先看队首请求需要多少资源，再决定是否出队：
    def peek_next(self) -> InferenceRequest:    # 只取出
        if not self._queue:
            raise RequestQueueEmptyError("队列为空")

        return self._queue[0]

    def __len__(self) -> int:
        return len(self._queue)