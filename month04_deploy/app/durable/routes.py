from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .idempotency import IdempotencyConflictError
from .task_store import TaskStore


TaskStatusValue = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]


class EchoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal["demo.echo"]
    payload: EchoPayload


class TaskAccepted(BaseModel):
    task_id: UUID
    status: TaskStatusValue
    status_url: str
    result: Any = None
    error: Any = None


class TaskView(BaseModel):
    task_id: UUID
    task_type: str
    payload: dict[str, Any]
    status: TaskStatusValue
    result: Any = None
    error: Any = None


def create_task_router(
    store: TaskStore | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/v1/tasks",
        tags=["durable-tasks"],
    )

    def require_store() -> TaskStore:
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="task_store_not_configured",
            )
        return store

    @router.post(
        "",
        status_code=202,
        response_model=TaskAccepted,
        responses={
            200: {
                "model": TaskAccepted,
                "description": "返回已经结束的已有任务",
            },
        },
    )
    def submit_task(
        body: CreateTaskRequest,
        response: Response,
        idempotency_key: Annotated[
            str | None,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
            ),
        ] = None,
    ) -> TaskAccepted:
        task_store = require_store()
        payload = body.payload.model_dump()

        try:
            if idempotency_key is None:
                row = task_store.create_task(
                    task_type=body.task_type,
                    payload=payload,
                )
            else:
                # TODO 1：
                # 调用 task_store.create_task_idempotent()。
                # 使用关键字参数：
                # task_type、payload、idempotency_key。
                # 将返回值赋给 row。
                # raise NotImplementedError("完成 TODO 1")
                row = task_store.create_task_idempotent(
                    task_type=body.task_type,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )

        except IdempotencyConflictError as exc:
            # TODO 2：
            # 抛出 HTTPException，并保留异常链。
            # status_code=409
            # detail="idempotency_conflict"
            # raise NotImplementedError("完成 TODO 2") from exc
            raise HTTPException(
                status_code=409,
                detail="idempotency_conflict",
            ) from exc

        except psycopg.OperationalError as exc:
            raise HTTPException(
                status_code=503,
                detail="database_operation_failed",
            ) from exc

        # 存储方法已经正常退出事务上下文，
        # 此时才能构造成功响应。
        status_url = f"/v1/tasks/{row['task_id']}"
        response.headers["Location"] = status_url

        if row["status"] in {"succeeded", "failed", "cancelled"}:
            response.status_code = 200
        else:
            response.status_code = 202

        return TaskAccepted(
            task_id=row["task_id"],
            status=row["status"],
            status_url=status_url,
            result=row.get("result"),
            error=row.get("error"),
        )
    @router.get("/{task_id}", response_model=TaskView)
    def query_task(task_id: UUID) -> TaskView:
        task_store = require_store()

        try:
            # TODO 2：
            # 调用 task_store.get_task(task_id)，返回值赋给 row。
            row = task_store.get_task(task_id=task_id)
        except psycopg.OperationalError as exc:
            raise HTTPException(
                status_code=503,
                detail="database_operation_failed",
            ) from exc

        if row is None:
            # TODO 3：
            # 抛出 HTTPException：
            # status_code=404，detail="task_not_found"。
            raise HTTPException(
                status_code=404,
                detail="task_not_found",
            )

        return TaskView.model_validate(row)

    return router