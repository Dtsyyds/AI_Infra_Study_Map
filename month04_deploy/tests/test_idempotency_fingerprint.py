import pytest

from month04_deploy.app.durable.idempotency import (
    build_request_fingerprint,
)


def test_same_request_has_same_fingerprint():
    first = build_request_fingerprint(
        "demo.echo",
        {"text": "你好"},
    )
    second = build_request_fingerprint(
        "demo.echo",
        {"text": "你好"},
    )

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_dictionary_order_does_not_change_fingerprint():
    # 这里只测试通用 JSON 指纹函数，
    # 不表示 demo.echo API 已支持这些额外字段。
    first = {
        "text": "hello",
        "options": {"language": "zh", "style": "short"},
    }
    second = {
        "options": {"style": "short", "language": "zh"},
        "text": "hello",
    }

    assert build_request_fingerprint(
        "demo.echo", first
    ) == build_request_fingerprint(
        "demo.echo", second
    )


@pytest.mark.parametrize(
    ("task_type", "payload"),
    [
        ("demo.other", {"text": "hello"}),
        ("demo.echo", {"text": "goodbye"}),
    ],
)
def test_changed_business_content_changes_fingerprint(
    task_type,
    payload,
):
    original = build_request_fingerprint(
        "demo.echo",
        {"text": "hello"},
    )

    changed = build_request_fingerprint(task_type, payload)

    assert changed != original


def test_string_whitespace_is_preserved():
    assert build_request_fingerprint(
        "demo.echo",
        {"text": "hello"},
    ) != build_request_fingerprint(
        "demo.echo",
        {"text": "hello "},
    )


def test_nan_is_rejected():
    with pytest.raises(ValueError):
        build_request_fingerprint(
            "demo.echo",
            {"value": float("nan")},
        )