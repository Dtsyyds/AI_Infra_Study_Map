from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class TaskStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        return psycopg.connect(
            self.dsn,
            row_factory=dict_row,
            connect_timeout=5,
        )

    def create_task(
        self,
        task_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = uuid4()

        sql = """
            INSERT INTO tasks (
                task_id,
                task_type,
                payload
            )
            VALUES (%s, %s, %s)
            RETURNING *
        """

        row = None

        with self._connect() as conn:
            # TODO 1：
            # 执行 sql，并通过 fetchone() 将返回记录赋给 row。
            # 三个参数依次为：
            # task_id、task_type、经过 Jsonb 包装的 payload。
            with conn.cursor() as cur:
                cur.execute(sql, (task_id, task_type, Jsonb(payload)))
                row = cur.fetchone()

            if row is None:
                raise RuntimeError("插入任务后没有返回记录")

        # 到达这里，连接上下文已经成功退出，事务已提交。
        return row

    def get_task(
        self,
        task_id: UUID,
    ) -> dict[str, Any] | None:
        sql = """
            SELECT *
            FROM tasks
            WHERE task_id = %s
        """

        row = None

        with self._connect() as conn:
            # TODO 2：
            # 执行参数化查询，用 fetchone() 将结果赋给 row。
            # 参数序列中只有 task_id 一个元素。
            with conn.cursor() as cur:
                cur.execute(sql, (task_id,))
                row = cur.fetchone()

        return row

    def try_start_task(
        self,
        task_id: UUID,
    ) -> dict[str, Any] | None:
        """仅当任务为 queued 时，将其改为 running。"""
        sql = """
            UPDATE tasks
            SET status = 'running',
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
              AND status = %s
            RETURNING *
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 执行 sql，两个参数依次是：
                # task_id、允许开始执行的当前状态。
                # 用 fetchone() 将返回值赋给 row。
                cur.execute(sql, (task_id, "queued"))
                row = cur.fetchone()

        # 提交成功后，才返回更新结果。
        return row

    def try_succeed_task(
        self,
        task_id: UUID,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """仅当任务为 running 时，原子保存成功状态和结果。"""
        sql = """
            UPDATE tasks
            SET status = 'succeeded',
                result = %s,
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
              AND status = %s
            RETURNING *
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 2：
                # 执行 sql，三个参数依次是：
                # Jsonb 包装的 result、task_id、
                # 允许提交成功结果的当前状态。
                # 用 fetchone() 将返回值赋给 row。
                cur.execute(sql, (Jsonb(result), task_id, "running"))
                row = cur.fetchone()

        return row

    def save_completed_step(
        self,
        task_id: UUID,
        step_index: int,
        step_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """插入一条已完成步骤记录，重复身份直接报错。"""
        sql = """
            INSERT INTO task_steps (
                task_id,
                step_index,
                step_name,
                result
            )
            VALUES (%s, %s, %s, %s)
            RETURNING *
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 参数依次为：
                # task_id、step_index、step_name、Jsonb(result)。
                # 执行后用 fetchone() 将返回值赋给 row。
                cur.execute(sql, (task_id, step_index, step_name, Jsonb(result)))
                row = cur.fetchone()

            if row is None:
                raise RuntimeError("插入步骤后没有返回记录")

        return row

    def get_completed_steps(
        self,
        task_id: UUID,
    ) -> list[dict[str, Any]]:
        """按步骤编号返回任务已经保存的全部步骤。"""
        sql = """
            SELECT *
            FROM task_steps
            WHERE task_id = %s
            ORDER BY step_index
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 2：
                # 参数序列只有 task_id。
                # 用 fetchall() 将所有返回记录赋给 rows。
                cur.execute(sql, (task_id,))
                rows = cur.fetchall()

        return rows