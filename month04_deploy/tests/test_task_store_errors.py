from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from month04_deploy.app.durable.task_store import TaskStore


def test_create_task_propagates_commit_error(monkeypatch):
    store = TaskStore("unused-in-this-test")

    # 模拟连接与游标，不访问真实数据库。
    conn = MagicMock()
    conn.__enter__.return_value = conn

    cursor = conn.cursor.return_value.__enter__.return_value

    # 锁定配置、检查排队名额、插入 Task 均成功，只有最后的提交失败。
    cursor.fetchone.side_effect = [
        {"max_queued_tasks": 32},
        {"queued_count": 0},
        {"task_id": uuid4(), "status": "queued"},
    ]

    # 但退出连接上下文时，模拟提交阶段发生异常。
    conn.__exit__.side_effect = psycopg.OperationalError(
        "simulated commit failure"
    )

    monkeypatch.setattr(store, "_connect", lambda: conn)

    # 调用方必须收到异常，不能收到创建成功的记录。
    with pytest.raises(
        psycopg.OperationalError,
        match="simulated commit failure",
    ):
        store.create_task(
            task_type="demo.echo",
            payload={"text": "test"},
        )

    assert cursor.fetchone.call_count == 3
    conn.__exit__.assert_called_once_with(None, None, None)