class KVCacheFullError(RuntimeError):
    """KV Cache 剩余容量不足"""

class KVCache:
    def __init__(self, *, capacity_tokens: int):
        if (
            isinstance(capacity_tokens, bool)
            or not isinstance(capacity_tokens, int)
            or capacity_tokens < 1
        ):
            raise ValueError("capacity_tokens 必须是正整数")
        
        self._capacity_tokens = capacity_tokens
        self._allocations: dict[str, int] = {}

    @property
    def capacity_tokens(self) -> int:
        return self._capacity_tokens

    @property
    def used_tokens(self) -> int:
        return sum(self._allocations.values())

    @property
    def available_tokens(self) -> int:
        return self.capacity_tokens - self.used_tokens

    def reserve(self, request_id: str, tokens: int) -> None:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id 必须是非空字符串")

        if (
            isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or tokens < 1
        ):
            raise ValueError("tokens 必须是正整数")

        if self.available_tokens < tokens:
            raise KVCacheFullError("KV Cache 剩余容量不足")

        self._allocations[request_id] = (
            self._allocations.get(request_id, 0) + tokens
        )

    def release(self, request_id: str) -> int:
        # 从字典 self._allocations 中取出 request_id 对应的值，并把这个键值对删除；如果 request_id 不存在，就返回默认值 0，并且不报错。
        return self._allocations.pop(request_id, 0)

    def tokens_for(self, request_id: str) -> int:
        return self._allocations.get(request_id, 0)
