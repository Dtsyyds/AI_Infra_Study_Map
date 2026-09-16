import pytest

from month03_llm_serving.paged_kv_cache import (
    PagedKVCache,
    PagedKVCacheFullError,
)

def test_paged_kv_cache_allocates_blocks_lazily_and_atomically():
    cache = PagedKVCache(
        capacity_tokens=16,
        block_size=4,
    )

    assert cache.total_blocks == 4
    assert cache.used_blocks == 0
    assert cache.free_blocks == 4

    # A 的前 3 个 Token 只需要一个 Block。
    cache.append_tokens("A", 3)

    assert cache.logical_tokens_for("A") == 3
    assert cache.block_table("A") == (0,)
    assert cache.used_blocks == 1

    # A 再增加 2 个 Token，总长度变成 5，
    # 跨过 Block 边界，因此增加一个 Block。
    cache.append_tokens("A", 2)

    assert cache.logical_tokens_for("A") == 5
    assert cache.block_table("A") == (0, 1)
    assert cache.allocated_tokens_for("A") == 8
    assert cache.used_blocks == 2

    # B 使用剩余两个 Block。
    cache.append_tokens("B", 8)

    assert cache.logical_tokens_for("B") == 8
    assert cache.block_table("B") == (2, 3)
    assert cache.used_blocks == 4
    assert cache.free_blocks == 0

    # A 再增加 4 个 Token 后总长度将变成 9，
    # 需要第三个 Block，但现在没有空闲 Block。
    with pytest.raises(PagedKVCacheFullError):
        cache.append_tokens("A", 4)

    # 分配失败必须保持原状态，不能只更新逻辑长度。
    assert cache.logical_tokens_for("A") == 5
    assert cache.block_table("A") == (0, 1)
    assert cache.used_blocks == 4

    # A 完成后释放两个 Block。
    assert cache.release("A") == 2

    assert cache.logical_tokens_for("A") == 0
    assert cache.block_table("A") == ()
    assert cache.used_blocks == 2
    assert cache.free_blocks == 2
