from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import APIConnectionError

import llm as llm_module
from llm import LLMExecutionTimeoutError, OpenAICompatibleLLM


class FakeClock:
    def __init__(self):
        self._now = 0.0

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


class RecordingCompletions:
    def __init__(self, clock):
        self.clock = clock
        self.received_timeouts = []

    def create(self, **kwargs):
        self.received_timeouts.append(kwargs["timeout"])

        if len(self.received_timeouts) == 1:
            # 第一次请求消耗 3 秒后失败。
            self.clock.advance(3)
            raise APIConnectionError(
                message="temporary failure",
                request=Mock(),
            )

        message = SimpleNamespace(content=("Thought: 重试成功\nAction: Finish[完成]"))
        choice = SimpleNamespace(message=message)

        return SimpleNamespace(choices=[choice])


def test_real_llm_retries_share_one_timeout_budget(
    monkeypatch,
):
    clock = FakeClock()
    completions = RecordingCompletions(clock)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setenv(
        "Agent_AI_API_KEY",
        "test-key",
    )
    monkeypatch.setenv(
        "Agent_AI_BASE_URL",
        "https://example.test",
    )
    monkeypatch.setenv(
        "Agent_AI_MODEL_NAME",
        "test-model",
    )

    monkeypatch.setattr(
        llm_module,
        "OpenAI",
        lambda **_kwargs: fake_client,
    )

    # 当前代码可能还没有导入 monotonic，
    # raising=False 允许测试先定义预期接口。
    monkeypatch.setattr(
        llm_module,
        "monotonic",
        clock,
        raising=False,
    )

    llm = OpenAICompatibleLLM()

    result = llm.generate(
        "测试 prompt",
        timeout_seconds=5,
    )

    assert result == ("Thought: 重试成功\nAction: Finish[完成]")

    # 第一次得到完整 5 秒；
    # 消耗 3 秒后，第二次只剩 2 秒。
    assert completions.received_timeouts == [
        5,
        2,
    ]


class BudgetExhaustingCompletions:
    def __init__(self, clock):
        self.clock = clock
        self.received_timeouts = []

    def create(self, **kwargs):
        self.received_timeouts.append(kwargs["timeout"])

        if len(self.received_timeouts) > 1:
            raise AssertionError("预算耗尽后不应再次调用 HTTP 客户端")

        self.clock.advance(5)
        raise APIConnectionError(
            message="temporary failure",
            request=Mock(),
        )


def test_real_llm_does_not_retry_when_budget_is_exhausted(monkeypatch):
    clock = FakeClock()
    completions = BudgetExhaustingCompletions(clock)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setenv(
        "Agent_AI_API_KEY",
        "test-key",
    )
    monkeypatch.setenv(
        "Agent_AI_BASE_URL",
        "https://example.test",
    )
    monkeypatch.setenv(
        "Agent_AI_MODEL_NAME",
        "test-model",
    )

    monkeypatch.setattr(
        llm_module,
        "OpenAI",
        lambda **_kwargs: fake_client,
    )

    # 当前代码可能还没有导入 monotonic，
    # raising=False 允许测试先定义预期接口。
    monkeypatch.setattr(
        llm_module,
        "monotonic",
        clock,
        raising=False,
    )

    llm = OpenAICompatibleLLM()

    with pytest.raises(LLMExecutionTimeoutError):
        llm.generate("测试 prompt", timeout_seconds=5)

    assert completions.received_timeouts == [5]
