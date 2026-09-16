from collections import deque

class PagedKVCacheFullError(RuntimeError):
    """Paged KV Cache 没有足够的空闲 Block。"""

class PagedKVCache:
    """ 页面化键值缓存，支持原子块分配。 """
    def __init__(
            self, 
            *, 
            capacity_tokens: int,
            block_size: int,
            ):
        """
        初始化缓存。

        :param capacity_tokens: 缓存总容量（以 Token 为单位）。
        :param block_size: 单个 Block 的大小（以 Token 为单位）。
        """
        if (
            isinstance(capacity_tokens, bool)
            or not isinstance(capacity_tokens, int)
            or capacity_tokens < 1
        ):
            raise ValueError("capacity_tokens 必须是正整数。")

        if(
            isinstance(block_size, bool)
            or not isinstance(block_size, int)
            or block_size < 1
        ):
            raise ValueError("block_size 必须是正整数。")

        if(capacity_tokens % block_size != 0):
            raise ValueError("capacity_tokens 必须是 block_size 的倍数。")
        
        self._capacity_tokens = capacity_tokens
        self._block_size = block_size

        self._logical_tokens: dict[str, int] = {}           # 请求真正拥有多少有效 Token
        self._block_tables: dict[str, list[int]] = {}       # 请求对应哪些物理 Block
        self._free_block_ids: deque[int] = deque(range(self.total_blocks))               # 当前尚未分配的物理 Block 编号

    @property
    def capacity_tokens(self) -> int:
        return self._capacity_tokens
    
    @property
    def total_blocks(self) -> int:
        """ 缓存总共可以容纳多少个 Block。 """
        return self._capacity_tokens // self._block_size

    @property
    def used_blocks(self) -> int:
        """ 当前缓存已经使用了多少个 Block。 """
        return self.total_blocks - len(self._free_block_ids)

    @property
    def free_blocks(self) -> int:
        """ 当前缓存还有多少个空闲 Block。 """
        return len(self._free_block_ids)

    @property
    def logical_tokens(self) -> int:
        """ 当前缓存总共拥有多少有效 Token。 """
        return sum(self._logical_tokens.values())

    @property
    def allocated_tokens(self) -> int:
        """ 当前缓存总共分配了多少 Token。 """
        return self.used_blocks * self._block_size

    def logical_tokens_for(self, request_id: str) -> int:
        """ 返回请求拥有的有效 Token 数。 """
        return self._logical_tokens.get(request_id, 0)
    
    def block_table(self, request_id: str) -> tuple[int, ...]:
        """ 返回请求对应的物理 Block 编号列表。 """
        return tuple(self._block_tables.get(request_id, []))

    def append_tokens(self, request_id: str, tokens: int) -> None:
        """ 为请求分配更多 Token。 """
        if not isinstance(request_id, str) or not request_id:
            raise ValueError(
                "request_id 必须是非空字符串"
            )
        if (
            isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or tokens < 1
        ):
            raise ValueError("tokens 必须是正整数。")

        # 计算新的逻辑 Token 数量
        request_tokens =  self._logical_tokens.get(request_id, 0) + tokens
        # 计算需要的总 Block 数量
        required_blocks = (request_tokens + self._block_size - 1) // self._block_size
        # 额外需要的 Block 数量
        additional_blocks = required_blocks - len(self._block_tables.get(request_id, []))
        # 检查是否有足够的空闲 Block
        if (
            self.free_blocks < additional_blocks
            or request_tokens > self._capacity_tokens
        ):
            raise PagedKVCacheFullError("缓存已满，无法分配更多空间。")
        # 一次性分配 Block
        # 确保列表存在
        block_table = self._block_tables.setdefault(request_id, [])
        for _ in range(additional_blocks):
            block_table.append(self._free_block_ids.popleft())
        # 更新逻辑 Token 数量
        self._logical_tokens[request_id] = request_tokens

    def allocated_tokens_for(self, request_id: str) -> int:
        """ 返回请求总共分配了多少 Token。 """
        return len(self._block_tables.get(request_id, [])) * self._block_size

    def release(self, request_id: str) -> int:
        """ 释放请求占用的 Block，返回释放的 Block 数量。 """
        block_table = self._block_tables.pop(request_id, [])
        self._logical_tokens.pop(request_id, None)
        self._free_block_ids.extend(block_table)
        return len(block_table)
