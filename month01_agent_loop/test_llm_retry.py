from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import (
    AuthenticationError,  # HTTP 401
    BadRequestError,  # HTTP 400
    InternalServerError,  # HTTP 500
    RateLimitError,  # HTTP 429
)

import llm as llm_module
from llm import LLMExecutionTimeoutError, OpenAICompatibleLLM


@pytest.fixture(autouse=True)
def fake_llm_environment(monkeypatch):
    monkeypatch.setenv("Agent_AI_API_KEY", "test-key")
    monkeypatch.setenv("Agent_AI_BASE_URL", "https://example.test")
    monkeypatch.setenv("Agent_AI_MODEL_NAME", "test-model")


def test_real_llm_disables_sdk_retries(monkeypatch):
    captured = {}
    fake_client = object()

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(llm_module, "OpenAI", fake_openai)

    OpenAICompatibleLLM()

    assert captured.get("max_retries") == 0


def test_real_llm_does_not_retry_programming_error(monkeypatch):
    call_count = 0
    original_error = TypeError("模拟代码错误")

    def fake_create(**_kwargs):
        nonlocal call_count
        call_count += 1
        raise original_error

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    monkeypatch.setattr(
        llm_module,
        "OpenAI",
        lambda **_kwargs: fake_client,
    )

    llm = OpenAICompatibleLLM()

    with pytest.raises(TypeError) as exc_info:
        llm.generate("测试 prompt", timeout_seconds=5)

    assert exc_info.value is original_error
    assert call_count == 1


@pytest.mark.parametrize(
    ("error_class", "status_code", "should_retry"),
    [
        (BadRequestError, 400, False),
        (AuthenticationError, 401, False),
        (InternalServerError, 500, True),
    ],
)
def test_real_llm_classifies_api_errors(
    monkeypatch,
    error_class,
    status_code,
    should_retry,
):
    response = Mock(
        status_code=status_code,
        request=Mock(),
        headers={},
    )
    original_error = error_class(
        "模拟 API 错误",
        response=response,
        body=None,
    )

    call_count = 0
    expected_text = "Thought: 请求成功\nAction: Finish[完成]"

    def fake_create(**_kwargs):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            raise original_error

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=expected_text,
                    )
                )
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(
        llm_module,
        "OpenAI",
        lambda **_kwargs: fake_client,
    )

    llm = OpenAICompatibleLLM()

    if should_retry:
        result = llm.generate(
            "测试 prompt",
            timeout_seconds=None,
        )
        assert result == expected_text
        assert call_count == 2
    else:
        with pytest.raises(error_class) as exc_info:
            llm.generate(
                "测试 prompt",
                timeout_seconds=None,
            )

        assert exc_info.value is original_error
        assert call_count == 1


@pytest.mark.parametrize(
    (
        "first_elapsed",
        "retry_after",
        "expected_timeouts",
        "expected_sleeps",
        "expect_timeout",
    ),
    [
        # 原有三个场景。
        pytest.param(
            2.0,
            "1",
            [5.0, 2.0],
            [1.0],
            False,
            id="valid_header",
        ),
        pytest.param(
            3.0,
            "2",
            [5.0],
            [],
            True,
            id="equal_budget",
        ),
        pytest.param(
            3.0,
            "3",
            [5.0],
            [],
            True,
            id="insufficient_budget",
        ),
        # 缺失或无效：使用当前阶段的固定 1 秒回退。
        pytest.param(
            2.0,
            None,
            [5.0, 2.0],
            [1.0],
            False,
            id="header_missing",
        ),
        pytest.param(
            2.0,
            "abc",
            [5.0, 2.0],
            [1.0],
            False,
            id="header_invalid_text",
        ),
        pytest.param(
            2.0,
            "-1",
            [5.0, 2.0],
            [1.0],
            False,
            id="header_negative",
        ),
        pytest.param(
            2.0,
            "nan",
            [5.0, 2.0],
            [1.0],
            False,
            id="header_non_finite",
        ),
        # 合法零值不能被替换成 1 秒。
        pytest.param(
            2.0,
            "0",
            [5.0, 3.0],
            [0.0],
            False,
            id="preserves_zero",
        ),
        # 回退等待也必须经过预算检查。
        pytest.param(
            4.0,
            None,
            [5.0],
            [],
            True,
            id="fallback_equals_budget",
        ),
        pytest.param(
            4.5,
            "abc",
            [5.0],
            [],
            True,
            id="fallback_exceeds_budget",
        ),
    ],
)
def test_rate_limit_retry_respects_shared_budget(
    monkeypatch,
    first_elapsed,
    retry_after,
    expected_timeouts,
    expected_sleeps,
    expect_timeout,
):
    clock = SimpleNamespace(now=0.0)
    received_timeouts = []
    sleep_calls = []

    expected_text = "Thought: 重试成功\nAction: Finish[完成]"

    headers = {} if retry_after is None else {"Retry-After": retry_after}

    rate_limit_error = RateLimitError(
        "模拟临时限流",
        response=Mock(
            status_code=429,
            request=Mock(),
            headers=headers,
        ),
        body={"code": "rate_limit_exceeded"},
    )

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # 模拟等待耗时，不让测试真的睡眠。
        clock.now += seconds

    def fake_create(**kwargs):
        received_timeouts.append(kwargs["timeout"])

        if len(received_timeouts) == 1:
            # 第一次请求消耗时间，然后返回临时限流。
            clock.now += first_elapsed
            raise rate_limit_error

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=expected_text))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    monkeypatch.setattr(
        llm_module,
        "OpenAI",
        lambda **_kwargs: fake_client,
    )
    monkeypatch.setattr(
        llm_module,
        "monotonic",
        lambda: clock.now,
    )
    monkeypatch.setattr(
        llm_module,
        "sleep",
        fake_sleep,
        raising=False,
    )

    llm = OpenAICompatibleLLM()

    if expect_timeout:
        with pytest.raises(LLMExecutionTimeoutError) as exc_info:
            llm.generate("测试 prompt", timeout_seconds=5)

        assert exc_info.value.__cause__ is rate_limit_error
    else:
        result = llm.generate("测试 prompt", timeout_seconds=5)
        assert result == expected_text

    assert received_timeouts == expected_timeouts
    assert sleep_calls == expected_sleeps


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("abc", None),
        ("-1", None),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
        ("0", 0.0),
        ("1", 1.0),
        ("1.5", 1.5),
    ],
)
def test_parse_retry_after_seconds(raw_value, expected):
    actual = llm_module._parse_retry_after_seconds(raw_value)

    if expected is None:
        assert actual is None
    else:
        assert actual == expected
