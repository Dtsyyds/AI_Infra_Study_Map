import pytest

import executor as executor_module
from agent import LLMAgent
from checkpoint import CheckpointStore


def test_resume_requires_existing_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODE", "fake")
    store = CheckpointStore(tmp_path / "checkpoints")
    agent = LLMAgent(checkpoint_store=store)

    with pytest.raises(ValueError, match="没有 checkpoint"):
        agent.run("计算 1 + 2", thread_id="missing-thread", resume=True)


def test_resume_skips_completed_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODE", "fake")
    base_dir = tmp_path / "checkpoints"
    thread_id = "resume-demo"
    calculator_calls = []
    original_run_tool = executor_module.run_tool

    def counted_run_tool(tool_name, **kwargs):
        if tool_name == "calculator":
            calculator_calls.append(dict(kwargs))
        return original_run_tool(tool_name, **kwargs)

    # 在真实工具入口计数；Finish 不经过 run_tool。
    monkeypatch.setattr(executor_module, "run_tool", counted_run_tool)
    first_agent = LLMAgent(max_steps=5, checkpoint_store=CheckpointStore(base_dir))

    with pytest.raises(RuntimeError, match="SIMULATED_CRASH after step 0"):
        first_agent.run("计算 1 + 2", thread_id=thread_id, simulate_crash_after_step=0)

    assert len(calculator_calls) == 1

    # 重新创建 Store 和 Agent，恢复数据来自磁盘。
    recovered_store = CheckpointStore(base_dir)
    checkpoint = recovered_store.load_latest(thread_id)
    assert checkpoint is not None
    assert checkpoint["state"]["next_step"] == 1
    assert checkpoint["state"]["user_input"] == "计算 1 + 2"

    recovered_agent = LLMAgent(max_steps=5, checkpoint_store=recovered_store)
    answer = recovered_agent.run("恢复时应使用保存的原始输入", thread_id=thread_id, resume=True)
    trace = recovered_agent.last_trace.snapshot()

    assert answer == "工具执行结果是:3"
    assert trace["status"] == "success"
    assert trace["user_input"] == "计算 1 + 2"
    assert trace["steps"][0]["step_index"] == 1
    assert len(calculator_calls) == 1


def test_load_latest_selects_highest_checkpoint_id(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoints")
    for step in [10, 9]:
        store.save(
            {
                "thread_id": "ordered-thread",
                "checkpoint_id": f"cp-{step:06d}",
                "state": {"next_step": step},
            }
        )
    thread_dir = tmp_path / "checkpoints" / "ordered-thread"
    (thread_dir / ".cp-999999.interrupted.tmp").write_text("incomplete")

    checkpoint = store.load_latest("ordered-thread")
    assert checkpoint is not None
    assert checkpoint["checkpoint_id"] == "cp-000010"
    assert checkpoint["state"]["next_step"] == 10


@pytest.mark.parametrize("field", ["thread_id", "checkpoint_id"])
@pytest.mark.parametrize("invalid_id", ["../outside", "/tmp/outside", "", "a/b"])
def test_save_rejects_invalid_identifiers(tmp_path, field, invalid_id):
    base_dir = tmp_path / "checkpoints"
    store = CheckpointStore(base_dir)
    checkpoint = {"thread_id": "safe-thread", "checkpoint_id": "cp-000001", "state": {}}
    checkpoint[field] = invalid_id

    with pytest.raises(ValueError, match=field):
        store.save(checkpoint)

    assert list(base_dir.iterdir()) == []


def test_save_rejects_thread_directory_symlink_escape(tmp_path):
    base_dir = tmp_path / "checkpoints"
    store = CheckpointStore(base_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (base_dir / "linked-thread").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="存储目录"):
        store.save({"thread_id": "linked-thread", "checkpoint_id": "cp-000001", "state": {}})

    assert list(outside.iterdir()) == []
