from typing import Any
from uuid import UUID, uuid4
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .idempotency import (
    IdempotencyConflictError,
    build_request_fingerprint,
)

from .retry_policy import RetryDecision, decide_retry

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
        *,
        retry_deadline_at: datetime | None = None,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        if retry_deadline_at is not None:
            # 检查时区
            if (
                retry_deadline_at.tzinfo is None
                or retry_deadline_at.utcoffset() is None
            ):
                raise ValueError("retry_deadline_at 必须带时区")
            
        if type(max_attempts) is not int:
            raise TypeError("传入的 max_attempts 数据类型错误")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须是大于 0 的整数")
        task_id = uuid4()

        sql = """
            INSERT INTO tasks (
                task_id,
                task_type,
                payload,
                retry_deadline_at,
                max_attempts
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
        """

        row = None

        with self._connect() as conn:
            # TODO 1：
            # 执行 sql，并通过 fetchone() 将返回记录赋给 row。
            # 三个参数依次为：
            # task_id、task_type、经过 Jsonb 包装的 payload。
            with conn.cursor() as cur:
                cur.execute(sql, (task_id, task_type, Jsonb(payload), retry_deadline_at, max_attempts))
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
        *,
        retry_deadline_at: datetime | None = None,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        """创建任务，或复用相同 key、相同内容的已有任务。"""
        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 128
        ):
            raise ValueError("idempotency_key 长度必须为 1～128")
        if retry_deadline_at is not None:
            # 检查时区
            if (
                retry_deadline_at.tzinfo is None
                or retry_deadline_at.utcoffset() is None
            ):
                raise ValueError("retry_deadline_at 必须带时区")
            
        if type(max_attempts) is not int:
            raise TypeError("传入的 max_attempts 数据类型错误")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须是大于 0 的整数")

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
                request_fingerprint,
                retry_deadline_at,
                max_attempts
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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
                        retry_deadline_at,
                        max_attempts
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
                    if (
                        row["request_fingerprint"] != fingerprint
                        or row["retry_deadline_at"] != retry_deadline_at
                        or row["max_attempts"] != max_attempts
                    ):
                        raise IdempotencyConflictError(
                            "幂等键已存在，但请求内容不一致"
                        )

        # 必须退出连接上下文、提交成功后再返回。
        return row
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

    def try_fail_task_with_attempt(
        self,
        task_id: UUID,
        attempt_id: UUID,
        error: dict[str, Any],
    ) -> dict[str, Any] | None:
        """在同一事务中记录任务及其对应 Attempt 的失败。"""

        with self._connect() as conn:
            with conn.cursor() as cur:
                # TODO 1：条件更新 Task。
                #
                # 更新上表中的 Task 字段。
                # 条件：
                #   1. task_id 匹配；
                #   2. Task 当前为 running；
                #   3. EXISTS 指定 attempt_id 的 running Attempt，
                #      且它属于这个 Task。
                #
                # 使用 RETURNING，fetchone() 赋给 task_row。
                # 如果 task_row 为 None，返回 None。
                # raise NotImplementedError("完成 Task 失败更新")
                cur.execute(
                    """
                    UPDATE tasks AS t
                    SET status = 'failed',
                        updated_at = CURRENT_TIMESTAMP,
                        result = NULL,
                        error = %s
                    WHERE task_id = %s
                        AND status = 'running'
                        AND EXISTS (
                            SELECT 1
                            FROM task_attempts AS a
                            WHERE a.task_id = t.task_id
                            AND a.attempt_id = %s
                            AND a.status = 'running'
                        )
                    RETURNING *
                    """,
                    (Jsonb(error), task_id, attempt_id),
                )
                task_row = cur.fetchone()
                if task_row is None:
                    return None
                # TODO 2：使用同一个 cur 条件更新 Attempt。
                #
                # 更新上表中的 Attempt 字段。
                # 条件：attempt_id、task_id 均匹配，且当前为 running。
                #
                # 使用 RETURNING，fetchone() 赋给 attempt_row。
                # 如果 attempt_row 为 None，抛出 RuntimeError，
                # 让前面的 Task 更新一起回滚。
                # raise NotImplementedError("完成 Attempt 失败更新")
                cur.execute(
                    """
                    UPDATE task_attempts
                    SET status = 'failed',
                        finished_at = CURRENT_TIMESTAMP,
                        error = %s
                    WHERE attempt_id = %s
                        AND task_id = %s
                        AND status = 'running'
                    RETURNING *
                    """,
                    (Jsonb(error), attempt_id, task_id),
                )
                attempt_row = cur.fetchone()
                if attempt_row is None:
                    raise RuntimeError(
                        "尝试更新 Attempt 后没有返回记录"
                    )

        # 提交成功后，返回已标记为 failed 的 Task。
        return task_row

    def try_retry_task_with_attempt(
        self,
        task_id: UUID,
        expected_attempt_id: UUID,
        *,
        retryable_error: bool,
        outcome_unknown: bool,
        replay_safe: bool,
    ):
        """重试领取的练习骨架，尚未完成。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                task_row = cur.fetchone()

                if task_row is None or task_row["status"] != "failed":
                    return None

                # TODO：补在这里
                # 使用同一个 cur 查询这个 Task 的最新 Attempt。
                # 按 attempt_no 降序，只取一条。
                # fetchone() 的结果赋给 latest_attempt。
                cur.execute(
                    """
                    SELECT * FROM task_attempts
                    WHERE task_id = %s
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                latest_attempt = cur.fetchone()

                if latest_attempt is None:
                    raise RuntimeError("无最新 Attempt 记录")

                if latest_attempt["status"] != "failed":
                    raise RuntimeError("最新 Attempt 不是 failed")

                if latest_attempt["attempt_id"] != expected_attempt_id:
                    return None

                # 此处已经持有 Task 行锁，并完成了最新 Attempt 校验。
                # 此处已经持有 Task 行锁，并完成最新 Attempt 校验。
                retry_deadline_at = task_row["retry_deadline_at"]

                cur.execute("SELECT clock_timestamp() AS checked_at")
                clock_row = cur.fetchone()
                checked_at = clock_row["checked_at"]

                if retry_deadline_at is None:
                    time_budget_remaining = False
                else:
                    time_budget_remaining = checked_at < retry_deadline_at

                decision = decide_retry(
                    retryable_error=retryable_error,
                    outcome_unknown=outcome_unknown,
                    replay_safe=replay_safe,
                    attempts_used=latest_attempt["attempt_no"],
                    max_attempts=task_row["max_attempts"],
                    time_budget_remaining=time_budget_remaining,
                )

                if decision is not RetryDecision.RETRY:
                    return decision

                task_sql = """
                    UPDATE tasks AS t
                    SET status = 'running',
                        result = NULL,
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE t.task_id = %s
                    AND t.status = 'failed'
                    RETURNING t.*
                """
                new_attempt_no = latest_attempt["attempt_no"] + 1
                attempt_sql = """
                    INSERT INTO task_attempts (
                        attempt_id,
                        task_id,
                        attempt_no,
                        status
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                """
                # TODO 1：更新 Task。
                # 使用当前 cur 执行 task_sql，参数为 (task_id,)。
                # fetchone() 赋给 task_row，覆盖前面查询到的旧记录。
                # 如果 task_row 为 None：
                #     raise RuntimeError("重试更新 Task 后没有返回记录")
                # raise NotImplementedError("完成 Task 重试更新")
                cur.execute(
                    task_sql,
                    (task_id,)
                )
                task_row = cur.fetchone()
                if task_row is None:
                    raise RuntimeError("重试更新 Task 后没有返回记录")

                # TODO 2：创建新的 Attempt。
                # 使用同一个 cur 执行 attempt_sql。
                # 参数依次为：
                #   uuid4()、task_id、new_attempt_no、"running"
                # fetchone() 赋给 attempt_row。
                # 如果 attempt_row 为 None：
                #     raise RuntimeError("创建重试 Attempt 后没有返回记录")
                # raise NotImplementedError("完成新 Attempt 创建")
                cur.execute(
                    attempt_sql,
                    (uuid4(), task_id, new_attempt_no, "running",)
                )
                attempt_row = cur.fetchone()
                if attempt_row is None:
                    raise RuntimeError("创建重试 Attempt 后没有返回记录")

        # 放在两个 with 外面：提交成功后返回领取结果。
        return task_row, attempt_row
