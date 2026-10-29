"""三道敏感数据保护共用同一确定性过滤器，不是任意秘密的 DLP 保证。"""

import re
from collections.abc import Mapping
from typing import Any

from config.settings import settings

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:secret|password|credential|authorization|api[_-]?key|private[_-]?key|"
    r"database[_-]?url|(?:^|_)token$)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:SIMULATED_SECRET[A-Za-z0-9_:=.-]*|FAKE_SECRET[A-Za-z0-9_:=.-]*|"
    r"fake-secret-[A-Za-z0-9_-]+|(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{8,}|"
    r"Bearer\s+[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        clean = _SENSITIVE_TEXT.sub(REDACTED, value)
        for secret in (
            settings.user_token,
            settings.restricted_token,
            settings.ops_token,
            settings.database_url,
        ):
            literal = secret.get_secret_value()
            if literal:
                clean = clean.replace(literal, REDACTED)
        return clean
    return value


def guard_input(arguments: dict[str, Any]) -> dict[str, Any]:
    """派发前过滤；调用方仍必须对过滤后的参数执行 Schema 校验。"""
    cleaned: dict[str, Any] = redact(arguments)
    return cleaned


def guard_output(result: dict[str, Any]) -> dict[str, Any]:
    """返回后过滤，防止工具结果污染 checkpoint、响应或后续审计。"""
    cleaned: dict[str, Any] = redact(result)
    return cleaned
