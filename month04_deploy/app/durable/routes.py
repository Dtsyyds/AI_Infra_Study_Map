from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

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
    status: Literal["queued"]
    status_url: str


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

    @router.post("", status_code=202, response_model=TaskAccepted)
    def submit_task(
        body: CreateTaskRequest,
        response: Response,
    ) -> TaskAccepted:
        task_store = require_store()

        try:
            # TODO 1：
            # 调用 task_store.create_task()，返回记录赋给 row。
            # task_type 使用 body.task_type。
            # payload 使用 body.payload.model_dump()。
            row = task_store.create_task(
                task_type=body.task_type,
                payload=body.payload.model_dump(),
            )
        except psycopg.OperationalError as exc:
            raise HTTPException(
                status_code=503,
                detail="database_operation_failed",
            ) from exc

        # 只有 create_task 正常返回，才构造受理成功响应。
        status_url = f"/v1/tasks/{row['task_id']}"
        response.headers["Location"] = status_url

        return TaskAccepted(
            task_id=row["task_id"],
            status=row["status"],
            status_url=status_url,
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