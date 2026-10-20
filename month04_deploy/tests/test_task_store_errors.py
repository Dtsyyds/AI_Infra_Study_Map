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

    # INSERT RETURNING 已经返回非空记录。
    cursor.fetchone.return_value = {
        "task_id": uuid4(),
        "status": "queued",
    }

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

    cursor.fetchone.assert_called_once()
    conn.__exit__.assert_called_once_with(None, None, None)