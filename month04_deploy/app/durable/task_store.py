from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .idempotency import (
    IdempotencyConflictError,
    build_request_fingerprint,
)


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

    def create_task_idempotent(
        self,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """创建任务，或复用相同 key、相同内容的已有任务。"""

        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 128
        ):
            raise ValueError("idempotency_key 长度必须为 1～128")

        fingerprint = build_request_fingerprint(
            task_type,
            payload,
        )

        insert_sql = """
            INSERT INTO tasks (
                task_id,
                task_type,
                payload,
                idempotency_key,
                request_fingerprint
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
        """

        select_sql = """
            SELECT *
            FROM tasks
            WHERE idempotency_key = %s
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 执行 insert_sql，参数依次为：
                # uuid4()、task_type、Jsonb(payload)、
                # idempotency_key、fingerprint。
                # fetchone() 的结果赋给 row。
                # raise NotImplementedError("完成 TODO 1")
                cur.execute(
                    insert_sql,
                    (
                        uuid4(),
                        task_type,
                        Jsonb(payload),
                        idempotency_key,
                        fingerprint,
                    ),
                )
                row = cur.fetchone()

                if row is None:
                    cur.execute(
                        select_sql,
                        (idempotency_key,),
                    )
                    row = cur.fetchone()

                    if row is None:
                        raise RuntimeError(
                            "幂等键发生冲突，但未查询到原任务"
                        )

                    # TODO 2：
                    # 比较 row["request_fingerprint"]
                    # 与本次 fingerprint。
                    # 不一致时抛出 IdempotencyConflictError。
                    # 一致时继续执行，不修改原任务。
                    if row["request_fingerprint"] != fingerprint:
                        raise IdempotencyConflictError(
                            "幂等键已存在，但请求内容不一致"
                        )

        # 必须退出连接上下文、提交成功后再返回。
        return row

    def try_start_task_with_attempt(
        self,
        task_id: UUID,
    ) -> dict[str, Any] | None:
        """原子领取 queued 任务，并创建第 1 次执行尝试。"""

        update_sql = """
            UPDATE tasks
            SET status = 'running',
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
              AND status = 'queued'
            RETURNING *
        """

        insert_sql = """
            INSERT INTO task_attempts (
                attempt_id,
                task_id,
                attempt_no
            )
            VALUES (%s, %s, %s)
            RETURNING *
        """

        claimed = None

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 执行 update_sql，参数只有 task_id。
                # fetchone() 的结果赋给 task。
                # raise NotImplementedError("完成 TODO 1")
                cur.execute(update_sql, (task_id,))
                task = cur.fetchone()

                if task is not None:
                    # TODO 2：
                    # 使用同一个 cur 执行 insert_sql。
                    # 参数依次为 uuid4()、task_id、1。
                    # fetchone() 的结果赋给 attempt。
                    # raise NotImplementedError("完成 TODO 2")

                    cur.execute(insert_sql, (uuid4(), task_id, 1))
                    attempt = cur.fetchone()

                    if attempt is None:
                        raise RuntimeError("创建执行尝试后没有返回记录")

                    claimed = {
                        "task": task,
                        "attempt": attempt,
                    }

        # 任务状态和 Attempt 均提交成功后，才返回。
        return claimed

    def get_task_attempts(
        self,
        task_id: UUID,
    ) -> list[dict[str, Any]]:
        """按尝试序号查询一个任务的执行历史。"""

        sql = """
            SELECT *
            FROM task_attempts
            WHERE task_id = %s
            ORDER BY attempt_no
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 3：
                # 执行 sql，参数只有 task_id。
                # fetchall() 的结果赋给 rows。
                # raise NotImplementedError("完成 TODO 3")
                cur.execute(sql, (task_id,))
                rows = cur.fetchall()

        return rows

    def try_start_task_with_attempt(
        self,
        task_id: UUID,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """首次领取任务，并在同一事务中创建第 1 次 Attempt。"""
        attempt_id = uuid4()

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 使用当前 cur 执行条件 UPDATE：
                # 仅将指定 task_id 的 queued 任务改为 running，
                # 同时更新 updated_at，使用 RETURNING *。
                # fetchone() 的结果赋给 task_row。
                # raise NotImplementedError("完成 TODO 1")
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'running',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = %s
                      AND status = 'queued'
                    RETURNING *
                    """,
                    (task_id,),
                )
                task_row = cur.fetchone()

                # TODO 2：
                # 如果 task_row 为 None，直接返回 None。
                # 此时不能继续插入 Attempt。
                if task_row is None:
                    return None

                # raise NotImplementedError("完成 TODO 2")

                # TODO 3：
                # 使用同一个 cur 插入 task_attempts：
                # attempt_id、task_id、attempt_no=1、status='running'。
                # 使用 RETURNING *，结果赋给 attempt_row。
                # raise NotImplementedError("完成 TODO 3")

                cur.execute(
                    """
                    INSERT INTO task_attempts (attempt_id, task_id, attempt_no, status)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (attempt_id, task_id, 1, 'running'),
                )
                attempt_row = cur.fetchone()

                if attempt_row is None:
                    raise RuntimeError("插入 Attempt 后没有返回记录")

        # 成功退出连接上下文、完成提交后，才能返回领取结果。
        return task_row, attempt_row

    def try_succeed_task_with_attempt(
        self,
        task_id: UUID,
        attempt_id: UUID,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """在同一事务中完成任务及其对应的 Attempt。"""

        task_sql = """
            UPDATE tasks AS t
            SET status = 'succeeded',
                result = %s,
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE t.task_id = %s
            AND t.status = 'running'
            AND EXISTS (
                SELECT 1
                FROM task_attempts AS a
                WHERE a.task_id = t.task_id
                    AND a.attempt_id = %s
                    AND a.status = 'running'
            )
            RETURNING t.*
        """

        attempt_sql = """
            UPDATE task_attempts
            SET status = 'succeeded',
                error = NULL,
                finished_at = CURRENT_TIMESTAMP
            WHERE attempt_id = %s
            AND task_id = %s
            AND status = 'running'
            RETURNING *
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：
                # 执行 task_sql。
                # 参数依次为 Jsonb(result)、task_id、attempt_id。
                # fetchone() 的结果赋给 task_row。
                # 如果 task_row 为 None，使用你刚才的 A 分支。
                # raise NotImplementedError("完成 TODO 1")
                cur.execute(
                    task_sql,
                    (Jsonb(result), task_id, attempt_id),
                )
                task_row = cur.fetchone()
                if task_row is None:
                    return None

                # TODO 2：
                # 使用同一个 cur 执行 attempt_sql。
                # 参数依次为 attempt_id、task_id。
                # fetchone() 的结果赋给 attempt_row。
                # 如果 attempt_row 为 None，使用你刚才的 B 分支。
                # raise NotImplementedError("完成 TODO 2")
                cur.execute(
                    attempt_sql,
                    (attempt_id, task_id),
                )
                attempt_row = cur.fetchone()
                if attempt_row is None:
                    raise RuntimeError(
                        "尝试更新 Attempt 后没有返回记录"
                    )

        # 两次更新提交成功后，才返回任务结果。
        return task_row

    def get_running_attempt(
        self,
        task_id: UUID,
    ) -> dict[str, Any] | None:
        """查询任务当前尚未完成的 Attempt。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO：
                # 查询 task_attempts。
                # 条件：task_id 匹配，并且 status = 'running'。
                # 使用参数绑定，返回 fetchone() 的结果。
                # raise NotImplementedError("完成 running Attempt 查询")
                cur.execute(
                    """
                    SELECT * FROM task_attempts
                    WHERE task_id = %s AND status = 'running'
                    """,
                    (task_id,),
                )
                return cur.fetchone()