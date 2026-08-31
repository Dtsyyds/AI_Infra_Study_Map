import pytest

import main as main_module


class RecordingExecutor:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append(
            {
                "wait": wait,
                "cancel_futures": cancel_futures,
            }
        )


# 正常退出关闭线程池
def test_normal_shutdown_with_executor(monkeypatch):
    fake_executor = RecordingExecutor()

    def fake_run_cli(*args, **kwargs):
        # fake_executor.shutdown(wait=True)
        pass

    monkeypatch.setattr(main_module, "run_cli", fake_run_cli)

    monkeypatch.setattr(
        main_module,
        "ThreadPoolExecutor",
        lambda *args, **kwargs: fake_executor,
    )

    main_module.main()

    assert fake_executor.shutdown_calls == [
        {
            "wait": True,
            "cancel_futures": True,
        }
    ]


# CLI 异常关闭线程池
def test_cli_exception_shutdown_with_executor(monkeypatch):
    fake_executor = RecordingExecutor()

    def failing_run_cli(*args, **kwargs):
        raise RuntimeError("cli failed")

    monkeypatch.setattr(main_module, "run_cli", failing_run_cli)
    monkeypatch.setattr(
        main_module,
        "ThreadPoolExecutor",
        lambda *args, **kwargs: fake_executor,
    )

    with pytest.raises(RuntimeError, match="cli failed"):
        main_module.main()

    assert fake_executor.shutdown_calls == [
        {
            "wait": True,
            "cancel_futures": True,
        }
    ]


def test_main_configures_bounded_tool_runtime(
    monkeypatch,
):
    captured = {}
    fake_executor = RecordingExecutor()
    fake_runtime = object()

    def fake_executor_factory(*, max_workers):
        captured["max_workers"] = max_workers
        return fake_executor

    def fake_runtime_factory(
        executor,
        *,
        capacity,
    ):
        captured["runtime_executor"] = executor
        captured["capacity"] = capacity
        return fake_runtime

    def fake_run_cli(agent):
        captured["agent"] = agent

    monkeypatch.setattr(
        main_module,
        "ThreadPoolExecutor",
        fake_executor_factory,
    )
    monkeypatch.setattr(
        main_module,
        "ToolRuntime",
        fake_runtime_factory,
    )
    monkeypatch.setattr(
        main_module,
        "run_cli",
        fake_run_cli,
    )

    main_module.main()

    assert captured["max_workers"] == 4
    assert captured["runtime_executor"] is fake_executor
    assert captured["capacity"] == 8
    assert captured["agent"].tool_runtime is fake_runtime
