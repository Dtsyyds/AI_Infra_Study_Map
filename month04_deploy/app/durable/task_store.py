import hashlib
import json
from typing import Any
from uuid import UUID, uuid4, uuid5
from datetime import datetime, timedelta

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

    def try_prepare_external_tool_call(
        self,
        task_id: UUID,
        expected_attempt_id: UUID,
        *,
        step_index: int,
        call_index: int,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        """登记外部调用意图。布尔值表示本次是否成功提交了新记录。"""
        for name, value in (
            ("step_index", step_index),
            ("call_index", call_index),
        ):
            if type(value) is not int:
                raise TypeError(f"{name} 必须是整数")
            if value < 0:
                raise ValueError(f"{name} 不能小于 0")

        if not isinstance(tool_name, str):
            raise TypeError("tool_name 必须是字符串")
        if not tool_name.strip():
            raise ValueError("tool_name 不能为空")
        if not isinstance(arguments, dict):
            raise TypeError("arguments 必须是字典")

        tool_call_id = uuid5(task_id, f"step:{step_index}:call:{call_index}")
        input_fingerprint = build_request_fingerprint(tool_name, arguments)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM tasks WHERE task_id = %s FOR UPDATE",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None or task_row["status"] != "running":
                    return None

                cur.execute(
                    """
                    SELECT attempt_id, status
                    FROM task_attempts
                    WHERE task_id = %s
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                latest_attempt = cur.fetchone()
                if latest_attempt is None:
                    raise RuntimeError("running Task 缺少 Attempt")
                if latest_attempt["status"] != "running":
                    raise RuntimeError("最新 Attempt 不是 running")
                if latest_attempt["attempt_id"] != expected_attempt_id:
                    return None

                cur.execute(
                    """
                    INSERT INTO tool_calls (
                        tool_call_id, task_id, step_index, call_index,
                        tool_name, input_fingerprint, status, result, completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'started', NULL, NULL)
                    ON CONFLICT (task_id, step_index, call_index) DO NOTHING
                    RETURNING *
                    """,
                    (
                        tool_call_id, task_id, step_index, call_index,
                        tool_name, input_fingerprint,
                    ),
                )
                call_row = cur.fetchone()
                inserted_here = call_row is not None

                if not inserted_here:
                    cur.execute(
                        """
                        SELECT *
                        FROM tool_calls
                        WHERE task_id = %s
                          AND step_index = %s
                          AND call_index = %s
                        """,
                        (task_id, step_index, call_index),
                    )
                    call_row = cur.fetchone()
                    if call_row is None:
                        raise RuntimeError("工具调用冲突后未找到账本")
                    if (
                        call_row["tool_name"] != tool_name
                        or call_row["input_fingerprint"] != input_fingerprint
                    ):
                        raise ValueError("同一工具调用的名称或参数发生变化")

        return call_row, inserted_here

    def try_succeed_external_tool_call(
        self,
        task_id: UUID,
        tool_call_id: UUID,
        *,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """保存已确认的外部回执，提交成功后返回该调用的实际账本行。"""
        if not isinstance(result, dict):
            raise TypeError("result 必须是字典")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tool_calls
                    SET status = 'succeeded',
                        result = %s,
                        completed_at = CURRENT_TIMESTAMP
                    WHERE task_id = %s
                      AND tool_call_id = %s
                      AND status = 'started'
                    RETURNING *
                    """,
                    (Jsonb(result), task_id, tool_call_id),
                )
                call_row = self._fetch_external_call_after_update(
                    cur, task_id, tool_call_id
                )

        return call_row

    def try_mark_external_tool_call_unknown(
        self,
        task_id: UUID,
        tool_call_id: UUID,
    ) -> dict[str, Any]:
        """发送方确认结果不明，或恢复方已确认原执行者退出且结果不可核对时调用。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tool_calls
                    SET status = 'unknown'
                    WHERE task_id = %s
                      AND tool_call_id = %s
                      AND status = 'started'
                    RETURNING *
                    """,
                    (task_id, tool_call_id),
                )
                call_row = self._fetch_external_call_after_update(
                    cur, task_id, tool_call_id
                )

        return call_row

    @staticmethod
    def _fetch_external_call_after_update(
        cur, task_id: UUID, tool_call_id: UUID,
    ) -> dict[str, Any]:
        """读取 UPDATE RETURNING，零行时回读同一调用的现状。"""
        call_row = cur.fetchone()
        if call_row is None:
            cur.execute(
                """
                SELECT *
                FROM tool_calls
                WHERE task_id = %s
                  AND tool_call_id = %s
                """,
                (task_id, tool_call_id),
            )
            call_row = cur.fetchone()
            if call_row is None:
                raise RuntimeError("已登记的工具调用缺少账本")

        return call_row

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

    def request_task_cancel(self, task_id: UUID) -> dict[str, Any] | None:
        """登记首次取消请求。等待执行的任务立即取消，正在执行的任务等 Worker 确认。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET cancel_requested_at = clock_timestamp(),
                        status = CASE WHEN status = 'running' THEN status ELSE 'cancelled' END,
                        updated_at = CASE WHEN status = 'running' THEN updated_at
                                          ELSE clock_timestamp() END,
                        next_retry_at = CASE WHEN status = 'running' THEN next_retry_at ELSE NULL END
                    WHERE task_id = %s
                      AND cancel_requested_at IS NULL
                      AND (
                          status IN ('queued', 'running')
                          OR (status = 'failed' AND next_retry_at IS NOT NULL)
                      )
                    RETURNING *
                    """,
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None:
                    # 重复请求或任务已结束时，返回数据库当前的状态和首次登记时间。
                    cur.execute("SELECT * FROM tasks WHERE task_id = %s", (task_id,))
                    task_row = cur.fetchone()

        return task_row

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
                # 兼容早期仅有 tasks 表的任务；已领取过 Attempt 的任务走带凭据的接口。
                cur.execute(
                    """
                    SELECT t.*, to_regclass('task_attempts') IS NOT NULL AS has_attempt_table
                    FROM tasks AS t
                    WHERE t.task_id = %s AND t.status = 'running'
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                task = cur.fetchone()
                if (
                    task is None
                    or task.get("lease_version", 0) != 0
                    or task.get("lease_expires_at") is not None
                ):
                    return None
                if task["has_attempt_table"]:
                    cur.execute(
                        "SELECT 1 FROM task_attempts WHERE task_id = %s LIMIT 1", (task_id,)
                    )
                    if cur.fetchone() is not None:
                        return None
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
                        lease_version = lease_version + 1,
                        lease_expires_at = clock_timestamp() + INTERVAL '10 seconds',
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
        *,
        expected_lease_version: int,
    ) -> dict[str, Any] | None:
        """在有效租约下同时提交 Task 和 Attempt 的成功结果。"""
        return self._finish_claimed_task(
            task_id, attempt_id, expected_lease_version, status="succeeded", result=result
        )

    @staticmethod
    def _lock_live_claim(
        cur, task_id: UUID, attempt_id: UUID, expected_lease_version: int,
    ) -> datetime | None:
        """先锁 Task，再锁对应的 running Attempt，返回有效租约的检查时间。"""
        cur.execute(
            """
            SELECT lease_expires_at FROM tasks
            WHERE task_id = %s AND status = 'running' AND lease_version = %s
            FOR UPDATE
            """,
            (task_id, expected_lease_version),
        )
        task = cur.fetchone()
        if task is None or task["lease_expires_at"] is None:
            return None

        cur.execute(
            """
            SELECT 1 FROM task_attempts
            WHERE task_id = %s AND attempt_id = %s AND status = 'running'
            FOR UPDATE
            """,
            (task_id, attempt_id),
        )
        if cur.fetchone() is None:
            return None

        # 拿到两把行锁之后再取时间，等待期间也可能到期。
        cur.execute("SELECT clock_timestamp() AS checked_at")
        checked_at = cur.fetchone()["checked_at"]
        if checked_at >= task["lease_expires_at"]:
            return None
        return checked_at

    def _finish_claimed_task(
        self,
        task_id: UUID,
        attempt_id: UUID,
        expected_lease_version: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                checked_at = self._lock_live_claim(
                    cur, task_id, attempt_id, expected_lease_version
                )
                if checked_at is None:
                    return None

                error_param = Jsonb(error) if error is not None else None
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = %s, result = %s, error = %s,
                        lease_expires_at = NULL, updated_at = %s
                    WHERE task_id = %s AND status = 'running' AND lease_version = %s
                    RETURNING *
                    """,
                    (
                        status, Jsonb(result) if result is not None else None, error_param,
                        checked_at, task_id, expected_lease_version,
                    ),
                )
                task = cur.fetchone()
                if task is None:
                    if status == "succeeded":
                        raise RuntimeError("completion rejected: Task update returned no row")
                    raise RuntimeError("failure recording rejected: Task update returned no row")

                cur.execute(
                    """
                    UPDATE task_attempts
                    SET status = %s, error = %s, finished_at = %s
                    WHERE task_id = %s AND attempt_id = %s AND status = 'running'
                    RETURNING attempt_id
                    """,
                    (status, error_param, checked_at, task_id, attempt_id),
                )
                if cur.fetchone() is None:
                    raise RuntimeError("尝试更新 Attempt 后没有返回记录")

        return task

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
        *,
        expected_lease_version: int,
    ) -> dict[str, Any] | None:
        """在有效租约下同时提交 Task 和 Attempt 的失败原因。"""
        return self._finish_claimed_task(
            task_id, attempt_id, expected_lease_version, status="failed", error=error
        )

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

                next_retry_at = task_row["next_retry_at"]

                if next_retry_at is None or checked_at < next_retry_at:
                    return None

                task_sql = """
                    UPDATE tasks AS t
                    SET status = 'running',
                        result = NULL,
                        error = NULL,
                        next_retry_at = NULL,
                        lease_version = t.lease_version + 1,
                        lease_expires_at = clock_timestamp() + INTERVAL '10 seconds',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE t.task_id = %s
                      AND t.status = 'failed'
                      AND t.next_retry_at IS NOT NULL
                      AND t.next_retry_at <= %s
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
                    (task_id, checked_at),
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

    def try_schedule_task_retry(
        self,
        task_id: UUID,
        expected_attempt_id: UUID,
        *,
        retryable_error: bool,
        outcome_unknown: bool,
        replay_safe: bool,
        delay_seconds: int = 5,
    ) -> dict[str, Any] | RetryDecision | None:
        """为最新失败的 Attempt 安排重试；当前练习尚未完成。"""

        if type(delay_seconds) is not int:
            raise TypeError("delay_seconds 必须是整数")
        if delay_seconds < 0:
            raise ValueError("delay_seconds 不能小于 0")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                task_row = cur.fetchone()

                if task_row is None or task_row["status"] != "failed":
                    return None

                # TODO 1：
                # 使用同一个 cur 查询这个 Task 的最新 Attempt。
                # 按 attempt_no 降序，LIMIT 1。
                # 查询时不要先筛选 status。
                # fetchone() 的结果赋给 latest_attempt。
                cur.execute(
                    """
                    SELECT *
                    From task_attempts
                    WHERE task_id = %s
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                latest_attempt = cur.fetchone()

                # TODO 2：
                # latest_attempt 为 None：
                #     抛出 RuntimeError("failed Task 缺少 Attempt")
                #
                # latest_attempt["status"] 不是 "failed"：
                #     抛出 RuntimeError("最新 Attempt 不是 failed")
                #
                # latest_attempt["attempt_id"] 与 expected_attempt_id 不同：
                #     返回 None。
                if latest_attempt is None:
                    raise RuntimeError("failed Task 缺少 Attempt")
                if latest_attempt["status"] != "failed":
                    raise RuntimeError("最新 Attempt 不是 failed")
                if latest_attempt["attempt_id"] != expected_attempt_id:
                    return None

                cur.execute("SELECT clock_timestamp() AS checked_at")
                checked_at = cur.fetchone()["checked_at"]

                retry_deadline_at = task_row["retry_deadline_at"]

                # TODO 1：
                # 截止时间为 None：time_budget_remaining = False。
                # 否则：比较 checked_at < retry_deadline_at。
                if retry_deadline_at is None:
                    time_budget_remaining = False
                else:
                    time_budget_remaining = checked_at < retry_deadline_at

                # TODO 2：
                # 调用 decide_retry()，结果赋给 decision。
                # 三个布尔参数使用方法入参。
                # attempts_used 使用 latest_attempt["attempt_no"]。
                # max_attempts 使用 task_row["max_attempts"]。
                # time_budget_remaining 使用上面的计算结果。
                decision = decide_retry(
                    retryable_error=retryable_error,
                    outcome_unknown=outcome_unknown,
                    replay_safe=replay_safe,
                    attempts_used=latest_attempt["attempt_no"],
                    max_attempts=task_row["max_attempts"],
                    time_budget_remaining=time_budget_remaining,
                )

                # TODO 3：
                # 如果 decision 不是 RetryDecision.RETRY，
                # 返回 decision。
                if decision != RetryDecision.RETRY:
                    return decision

                # 此处 decision 已经是 RetryDecision.RETRY。

                # TODO 1：
                # 如果 task_row["next_retry_at"] 不是 None，
                # 直接返回 task_row，保留原计划。
                #
                # 提示：使用 is not None 判断。
                if task_row["next_retry_at"] is not None:
                    return task_row


                # TODO 2：
                # 用 checked_at 加上 timedelta(seconds=delay_seconds)，
                # 结果赋给 next_retry_at。
                next_retry_at = checked_at + timedelta(seconds=delay_seconds)

                # TODO 3：
                # 如果 next_retry_at >= retry_deadline_at，
                # 返回 RetryDecision.STOP。
                #
                # 刚好到达截止时间，也不能安排这次重试。
                if next_retry_at >= retry_deadline_at:
                    return RetryDecision.STOP

                cur.execute(
                    """
                    UPDATE tasks
                    SET next_retry_at = %s
                    WHERE task_id = %s
                        AND status = 'failed'
                        AND next_retry_at IS NULL
                    RETURNING *
                    """,
                    (next_retry_at, task_id),
                )
                scheduled_task = cur.fetchone()

                if scheduled_task is None:
                    raise RuntimeError("安排重试时未更新到预期 Task")

        # 与最外层 with 对齐，事务提交成功后返回。
        return scheduled_task

    def try_increment_counter_once(
        self,
        task_id: UUID,
        expected_attempt_id: UUID,
        *,
        step_index: int,
        call_index: int,
        counter_name: str,
    ) -> dict[str, Any] | None:
        """当前 Attempt 执行计数器加一；同一逻辑调用返回已保存结果。"""

        for name, value in (
            ("step_index", step_index),
            ("call_index", call_index),
        ):
            if type(value) is not int:
                raise TypeError(f"{name} 必须是整数")
            if value < 0:
                raise ValueError(f"{name} 不能小于 0")

        if not isinstance(counter_name, str):
            raise TypeError("counter_name 必须是字符串")
        if not counter_name.strip():
            raise ValueError("counter_name 不能为空")

        tool_name = "demo.counter_increment_v1"

        # 相同 Task、步骤位置和调用位置，生成相同的 UUID。
        # expected_attempt_id 不参与生成。
        tool_call_id = uuid5(
            task_id,
            f"step:{step_index}:call:{call_index}",
        )

        fingerprint_payload = {
            "tool_name": tool_name,
            "counter_name": counter_name,
            "increment": 1,
        }
        input_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        with self._connect() as conn:
            with conn.cursor() as cur:
                # 先锁 Task，串行处理同一个 Task 的工具调用。
                cur.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                task_row = cur.fetchone()

                if task_row is None or task_row["status"] != "running":
                    return None

                # 拿到锁之后，校验当前运行中的 Attempt。
                cur.execute(
                    """
                    SELECT *
                    FROM task_attempts
                    WHERE task_id = %s
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                latest_attempt = cur.fetchone()

                if latest_attempt is None:
                    raise RuntimeError("running Task 缺少 Attempt")
                if latest_attempt["status"] != "running":
                    raise RuntimeError("最新 Attempt 不是 running")
                if latest_attempt["attempt_id"] != expected_attempt_id:
                    return None

                # 从这里开始，是你已经完成的核心逻辑。
                cur.execute(
                    """
                    SELECT *
                    FROM tool_calls
                    WHERE task_id = %s
                      AND step_index = %s
                      AND call_index = %s
                    """,
                    (task_id, step_index, call_index),
                )
                existing_call = cur.fetchone()

                if existing_call is not None:
                    if (
                        existing_call["tool_name"] != tool_name
                        or existing_call["input_fingerprint"]
                        != input_fingerprint
                    ):
                        raise ValueError(
                            "同一工具调用的名称或参数发生变化"
                        )

                    return existing_call["result"]

                cur.execute(
                    """
                    INSERT INTO demo_counters (counter_name, value)
                    VALUES (%s, 1)
                    ON CONFLICT (counter_name)
                    DO UPDATE SET value = demo_counters.value + 1
                    RETURNING value
                    """,
                    (counter_name,),
                )
                counter_row = cur.fetchone()

                if counter_row is None:
                    raise RuntimeError("计数器更新后没有返回记录")

                result = {
                    "counter_name": counter_name,
                    "value": counter_row["value"],
                }

                cur.execute(
                    """
                    INSERT INTO tool_calls (
                        tool_call_id,
                        task_id,
                        step_index,
                        call_index,
                        tool_name,
                        input_fingerprint,
                        result
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING result
                    """,
                    (
                        tool_call_id,
                        task_id,
                        step_index,
                        call_index,
                        tool_name,
                        input_fingerprint,
                        Jsonb(result),
                    ),
                )
                saved_call = cur.fetchone()

                if saved_call is None:
                    raise RuntimeError("写入工具账本后没有返回记录")

                result = saved_call["result"]

        # 两个 with 退出、事务提交成功后，返回新执行的结果。
        return result

    def try_renew_task_lease(
        self,
        task_id: UUID,
        expected_lease_version: int,
    ) -> dict[str, Any] | None:
        """为仍持有有效租约的 Worker 续租 10 秒。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id FROM tasks WHERE task_id = %s FOR UPDATE",
                    (task_id,),
                )
                if cur.fetchone() is None:
                    return None

                cur.execute(
                    """
                    UPDATE tasks
                    SET lease_expires_at = clock_timestamp() + INTERVAL '10 seconds'
                    WHERE task_id = %s
                      AND status = 'running'
                      AND lease_version = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING *
                    """,
                    (task_id, expected_lease_version),
                )
                renewed = cur.fetchone()

        return renewed

    def try_take_over_expired_task(
        self,
        task_id: UUID,
        expected_lease_version: int,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """尝试接管一个过期的任务。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lease_expires_at, max_attempts, retry_deadline_at
                    FROM tasks
                    WHERE task_id = %s AND status = 'running' AND lease_version = %s
                    FOR UPDATE
                    """,
                    (task_id, expected_lease_version),
                )
                task = cur.fetchone()
                if task is None:
                    return None

                cur.execute("SELECT clock_timestamp() AS checked_at")
                checked_at = cur.fetchone()["checked_at"]
                expiry = task["lease_expires_at"]
                if expiry is None or checked_at < expiry:
                    return None
                deadline = task["retry_deadline_at"]
                if deadline is not None and checked_at >= deadline:
                    return None

                cur.execute(
                    """
                    SELECT attempt_id, attempt_no, status
                    FROM task_attempts
                    WHERE task_id = %s
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                latest_attempt = cur.fetchone()
                if latest_attempt is None or latest_attempt["status"] != "running":
                    raise RuntimeError("接管时缺少预期的 running Attempt")
                if latest_attempt["attempt_no"] >= task["max_attempts"]:
                    return None

                cur.execute(
                    """
                    UPDATE tasks
                    SET lease_version = lease_version + 1,
                        lease_expires_at = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = %s
                      AND status = 'running'
                      AND lease_version = %s
                      AND lease_expires_at <= %s
                    RETURNING *
                    """,
                    (
                        checked_at + timedelta(seconds=10),
                        task_id,
                        expected_lease_version,
                        checked_at,
                    ),
                )
                taken_task = cur.fetchone()
                if taken_task is None:
                    return None

                cur.execute(
                    """
                    UPDATE task_attempts
                    SET status = 'interrupted', finished_at = %s
                    WHERE task_id = %s AND attempt_id = %s AND status = 'running'
                    RETURNING attempt_no
                    """,
                    (checked_at, task_id, latest_attempt["attempt_id"]),
                )
                old_attempt = cur.fetchone()
                if old_attempt is None:
                    raise RuntimeError("接管时未能将旧 Attempt 标记为 interrupted")
                new_attempt_no = old_attempt["attempt_no"] + 1

                cur.execute(
                    """
                    INSERT INTO task_attempts (
                        attempt_id, task_id, attempt_no, status, started_at
                    )
                    VALUES (%s, %s, %s, 'running', %s)
                    RETURNING *
                    """,
                    (uuid4(), task_id, new_attempt_no, checked_at),
                )
                new_attempt = cur.fetchone()

                if new_attempt is None:
                    raise RuntimeError("插入 Attempt 后没有返回记录")

        return taken_task, new_attempt

    def try_save_completed_step(
        self,
        task_id: UUID,
        step_index: int,
        step_name: str,
        result: dict[str, Any],
        *,
        expected_lease_version: int,
        expected_attempt_id: UUID,
    ) -> dict[str, Any] | None:
        """仅当 Worker 仍持有有效租约且 Attempt 正在运行时保存步骤。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if self._lock_live_claim(
                    cur, task_id, expected_attempt_id, expected_lease_version
                ) is None:
                    return None

                cur.execute(
                    """
                    INSERT INTO task_steps (
                        task_id, step_index, step_name, result
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (task_id, step_index, step_name, Jsonb(result)),
                )
                saved_step = cur.fetchone()
                if saved_step is None:
                    raise RuntimeError("保存步骤后没有返回记录")

        return saved_step