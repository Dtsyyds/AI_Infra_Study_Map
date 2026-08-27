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
