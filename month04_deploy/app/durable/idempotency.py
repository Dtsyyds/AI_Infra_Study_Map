import hashlib
import json
from typing import Any

class IdempotencyConflictError(ValueError):
    """同一个幂等键被用于不同的业务请求。"""
    
def build_request_fingerprint(
    task_type: str,
    payload: dict[str, Any],
) -> str:
    """为已校验的 JSON 任务描述生成稳定指纹。"""

    request_data = {
        "version": 1,
        "task_type": task_type,
        "payload": payload,
    }

    # TODO 1：
    # 使用 json.dumps() 编码 request_data。
    # 参数：
    # sort_keys=True
    # separators=(",", ":")
    # ensure_ascii=False
    # allow_nan=False
    # 将结果赋给 normalized。
    normalized = json.dumps(
        request_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False
    )

    # TODO 2：
    # 将 normalized 编码为 UTF-8 字节。
    # 将结果赋给 encoded。
    encoded = normalized.encode("utf-8")

    # TODO 3：
    # 使用 hashlib.sha256(encoded).hexdigest()，
    # 返回十六进制摘要字符串。
    return hashlib.sha256(encoded).hexdigest()