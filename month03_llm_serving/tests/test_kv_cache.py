import pytest

from month03_llm_serving.kv_cache import (
    KVCache,
    KVCacheFullError,
)

def test_kv_cache_reserves_and_releases_tokens():
    cache = KVCache(capacity_tokens=10)

    cache.reserve("A", 6)
    cache.reserve("B", 4)

    assert cache.used_tokens == 10
    assert cache.available_tokens == 0
    assert cache.tokens_for("A") == 6
    assert cache.tokens_for("B") == 4

    with pytest.raises(KVCacheFullError):
        cache.reserve("C", 1)

    assert cache.used_tokens == 10
    assert cache.tokens_for("C") == 0

    # 请求完成后释放其全部 KV Cache
    assert cache.release("A") == 6
    assert cache.used_tokens == 4
    assert cache.available_tokens == 6
    assert cache.tokens_for("A") == 0

    # 同一个请求允许增加 KV Token
    cache.reserve("B", 2)

    assert cache.tokens_for("B") == 6
    assert cache.used_tokens == 6