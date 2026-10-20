import json
import os
import re
import tempfile


class CheckpointStore:
    def __init__(self, base_dir="month01_agent_loop/checkpoints"):
        self.base_dir = os.path.realpath(os.fspath(base_dir))
        os.makedirs(self.base_dir, exist_ok=True)

    def save(self, checkpoint: dict) -> str:
        """
        保存 checkpoint。

        要求：
        1. 根据 thread_id 创建目录
        2. 根据 checkpoint_id 创建 JSON
        3. 使用 .tmp + os.replace()
        4. 返回最终文件路径
        """
        # 在创建目录和临时文件前校验两个标识符及目标路径。
        final_path = self._get_checkpoint_path(
            checkpoint["thread_id"],
            checkpoint["checkpoint_id"],
        )
        thread_dir = os.path.dirname(final_path)

        os.makedirs(
            thread_dir,
            exist_ok=True,
        )
        fd, tmp_path = tempfile.mkstemp(
            dir=thread_dir,
            prefix=f".{checkpoint['checkpoint_id']}.",
            suffix=".tmp",
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(
                tmp_path,
                final_path,
            )

            return final_path
        except Exception:
            os.remove(tmp_path)
            raise

    def load(
        self,
        thread_id: str,
        checkpoint_id: str,
    ) -> dict | None:
        """
        加载 checkpoint。

        要求：
        1. 根据 thread_id 和 checkpoint_id 构建路径
        2. 如果文件不存在，返回 None
        3. 如果文件存在，读取 JSON 并返回字典
        """
        path = self._get_checkpoint_path(thread_id, checkpoint_id)
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_latest(
        self,
        thread_id: str,
    ) -> dict | None:
        """
        加载最新的 checkpoint。

        要求：
        1. 根据 thread_id 构建目录
        2. 列出目录下所有 .json 文件
        3. 按 checkpoint_id 排序，取最新的一个
        4. 如果没有文件，返回 None
        5. 如果有文件，读取 JSON 并返回字典
        """
        thread_dir = self._get_thread_dir(thread_id)
        if not os.path.exists(thread_dir):
            return None

        checkpoint_files = [f for f in os.listdir(thread_dir) if f.endswith(".json")]
        if not checkpoint_files:
            return None

        latest_checkpoint_file = max(checkpoint_files)
        checkpoint_id = latest_checkpoint_file.removesuffix(".json")
        return self.load(thread_id, checkpoint_id)

    def _get_thread_dir(
        self,
        thread_id: str,
    ) -> str:
        self._validate_identifier(thread_id, "thread_id")
        return self._check_containment(os.path.join(self.base_dir, thread_id))

    def _get_checkpoint_path(
        self,
        thread_id: str,
        checkpoint_id: str,
    ) -> str:
        self._validate_identifier(checkpoint_id, "checkpoint_id")
        return self._check_containment(
            os.path.join(self._get_thread_dir(thread_id), f"{checkpoint_id}.json")
        )

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError(f"{name} 必须为非空字符串，只能包含英文字母、数字、下划线和连字符")

    def _check_containment(self, path: str) -> str:
        if os.path.commonpath([self.base_dir, os.path.realpath(path)]) != self.base_dir:
            raise ValueError("Checkpoint 路径不能超出存储目录")
        return path


def main():
    store = CheckpointStore()

    # 保存一个 checkpoint
    checkpoint_1 = {
        "version": 1,
        "thread_id": "test-thread",
        "run_id": "run-1",
        "checkpoint_id": "cp-000001",
        "status": "running",
        "next_node": "agent",
        "state": {
            "step_count": 1,
            "user_input": "计算 1 + 2",
        },
    }
    checkpoint_2 = {
        "version": 1,
        "thread_id": "test-thread",
        "run_id": "run-2",
        "checkpoint_id": "cp-000002",
        "status": "running",
        "next_node": "agent",
        "state": {
            "step_count": 2,
            "user_input": "计算 3 + 4",
        },
    }
    store.save(checkpoint_1)
    store.save(checkpoint_2)

    # 加载最新的 checkpoint
    latest = store.load_latest("test-thread")
    assert latest is not None
    assert latest["checkpoint_id"] == "cp-000002"
    assert latest["state"]["step_count"] == 2


if __name__ == "__main__":
    main()
