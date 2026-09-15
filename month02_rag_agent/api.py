from contextlib import asynccontextmanager
from typing import Literal

import logging
from threading import BoundedSemaphore

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict
from uuid import uuid4
from time import perf_counter, sleep

from month02_rag_agent.app import build_document_index
from month02_rag_agent.embedder import LocalEmbedder

from dataclasses import asdict
from functools import partial

from month02_rag_agent.task_manager import TaskManager, TaskQueueFullError


logger = logging.getLogger(__name__)
# __name__ 会使用当前模块名，例如 month02_rag_agent.api
# 后续日志系统可以根据模块名筛选日志


class CreateIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_path: str = Field(min_length=1, description="输入文档路径")
    output_path: str = Field(min_length=1, description="输出索引路径")
    chunk_size: int = Field(gt=0, description="每个文档的分块大小")
    overlap: int = Field(ge=0, description="分块之间的重叠大小")


class IndexSummary(BaseModel):
    status: Literal["created"]
    output_path: str
    schema_version: int
    model: str
    dimension: int
    normalized: bool
    record_count: int


class ServerBusyError(RuntimeError):
    """
    表示服务器当前没有可用的索引构建执行槽位。

    它不是未知程序错误，因此不能交给通用 Exception
    处理器转换成 INTERNAL_SERVER_ERROR。
    """


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    delay_seconds: int = Field(default=2.0, ge=0.0, le=10.0)


class CreateIndexTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_path: str = Field(min_length=1, description="输入文档路径")
    output_path: str = Field(min_length=1, description="输出索引路径")
    chunk_size: int = Field(gt=0, description="每个文档的分块大小")
    overlap: int = Field(ge=0, description="分块之间的重叠大小")


def demo_task_operation(
    text: str,
    delay_seconds: float,
) -> dict[str, str]:
    # 这个同步函数由 TaskManager 放在线程中执行。
    sleep(delay_seconds)
    return {"echo": text}


def build_index_task(
    payload: CreateIndexRequest,
    *,
    embedder: LocalEmbedder,
    index_build_slots: BoundedSemaphore,
) -> dict:
    # 等待并取得索引构建槽位，with 语句自动 acquire 和 release
    with index_build_slots:
        index = build_document_index(
            input_path=payload.input_path,
            output_path=payload.output_path,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
            embedder=embedder,
        )

        index_summary = IndexSummary(
            status="created",
            output_path=payload.output_path,
            schema_version=index["schema_version"],
            model=index["model"],
            dimension=index["dimension"],
            normalized=index["normalized"],
            record_count=len(index["records"]),
        )

    return index_summary.model_dump(mode="json")  # 转换成 JSON 可序列化的字典


# 使用 lifespan 管理 Embedder
def create_app(
    *,
    embedder_factory=LocalEmbedder,
    max_concurrent_index_builds: int = 1,
) -> FastAPI:
    if max_concurrent_index_builds < 1:
        raise ValueError("max_concurrent_index_builds 必须大于等于 1")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 每个 FastAPI 应用实例持有一个并发门控对象。
        #
        # value=1 表示同一时刻最多只有一个请求
        # 可以进入索引构建业务。
        app.state.index_build_slots = BoundedSemaphore(
            value=max_concurrent_index_builds
        )

        app.state.embedder = embedder_factory()

        manager = TaskManager(
            worker_count=2,
            queue_capacity=3,
        )

        app.state.task_manager = manager
        manager.start()

        try:
            yield
        finally:
            # 先处理完任务，再清理可能被任务使用的资源。
            await manager.close()

            app.state.task_manager = None
            app.state.embedder = None
            app.state.index_build_slots = None

    app = FastAPI(title="Month02 RAG API", lifespan=lifespan)

    # 装饰器不是立即执行函数，而是在应用创建时注册规则
    # 只要当前 app 中出现 FileNotFoundError,就调用 handle_file_not_found()
    @app.exception_handler(FileNotFoundError)
    async def handle_file_not_found(
        _request: Request, _exc: FileNotFoundError
    ) -> JSONResponse:
        # request 当前 HTTP 请求对象，可以获得 request.method/url.path/app/headers
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "INPUT_FILE_NOT_FOUND",
                    "message": "输入文件不存在",
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "type": error["type"],
                "message": error["msg"],
            }
            for error in _exc.errors()
        ]

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": "请求参数校验失败",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """
        将没有被更具体处理器捕获的异常转化为统一 500 响应
        """
        request_id = getattr(
            request.state,
            "request_id",
            "unknown",
        ) or str(uuid4())

        # 详细异常只能写入服务端日志
        # logger.exception() 会自动记录当前异常堆栈
        logger.exception(
            "Unexpected server error: request_id=%s, method=%s, path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "服务器内部错误",
                    "request_id": request_id,
                }
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    @app.exception_handler(ServerBusyError)
    async def handle_server_busy(
        request: Request, exc: ServerBusyError
    ) -> JSONResponse:
        request_id = getattr(
            request.state,
            "request_id",
            "unknown",
        )

        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "SERVICE_BUSY",
                    "message": "服务器繁忙，请稍后重试",
                    "request_id": request_id,
                }
            },
            headers={
                "X-Request-ID": request_id,
                "Retry-After": "5",
            },
        )

    @app.middleware("http")
    async def add_observability_middleware(request: Request, call_next):
        """
        为每个 HTTP 请求增加可观测性信息：

        1. 接收或生成 request_id
        2. 将 request_id 透传给下游服务
        """
        request_id = request.headers.get("X-Request-ID")
        # 生成 UUID 作为 request_id
        if request_id is None:
            request_id = str(uuid4())

        request.state.request_id = request_id
        start_time = perf_counter()
        response = await call_next(request)
        elapsed_time_ms = (perf_counter() - start_time) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["x-process-time-ms"] = (
            f"{elapsed_time_ms:.3f}"  # 格式化字符串，保留三位小数
        )
        return response

    """
    request.headers         客户端传入的请求头          原本就来自客户端
    request.state           服务内部单次请求上下文
    response.headers        服务端将要返回的响应头
    """

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:

        embedder = getattr(request.app.state, "embedder", None)

        if embedder is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": {
                        "code": "EMBEDDER_NOT_READY",
                        "message": "Embedding 模型尚未就绪",
                    }
                },
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "ready"},
            )

    @app.get("/v1/tasks/{task_id}")
    async def get_task(
        task_id: str,
        request: Request,
    ) -> JSONResponse:
        manager = request.app.state.task_manager
        try:
            record = manager.get_task(task_id)
        except KeyError:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": {
                        "code": "TASK_NOT_FOUND",
                        "message": f"任务 {task_id} 不存在",
                    }
                },
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=asdict(record),  # asdict(record) 把任务记录转换成字典
            headers={"Cache-Control": "no-store"},
        )

    # 添加 POST /v1/indexs 路由
    @app.post(
        "/v1/indexes",
        status_code=status.HTTP_201_CREATED,
        response_model=IndexSummary,
    )
    def create_index(
        payload: CreateIndexRequest,
        request: Request,
    ) -> IndexSummary:
        # 从当前 FastAPI 应用实例中取得信号量。
        index_build_slots = request.app.state.index_build_slots
        # blocking=False 表示：
        #
        # - 有槽位：立即获取，返回 True；
        # - 没槽位：立即失败，返回 False；
        # - 不让请求在线程池里无限等待。
        acquired = index_build_slots.acquire(
            blocking=False  # 不阻塞，立即返回 快速失败背压
        )
        if not acquired:
            raise ServerBusyError()
        try:
            index = build_document_index(
                input_path=payload.input_path,
                output_path=payload.output_path,
                chunk_size=payload.chunk_size,
                overlap=payload.overlap,
                embedder=request.app.state.embedder,
            )

            return IndexSummary(
                status="created",
                output_path=payload.output_path,
                schema_version=index["schema_version"],
                model=index["model"],
                dimension=index["dimension"],
                normalized=index["normalized"],
                record_count=len(index["records"]),
            )
        finally:
            index_build_slots.release()

    @app.post("/v1/tasks", status_code=status.HTTP_202_ACCEPTED)
    async def create_task(
        payload: CreateTaskRequest,
        request: Request,
    ) -> JSONResponse:
        manager = request.app.state.task_manager

        operation = partial(
            demo_task_operation,
            text=payload.text,
            delay_seconds=payload.delay_seconds,
        )  # ：绑定参数，得到一个以后再执行的零参数函数

        try:
            task_id = manager.submit(operation)
        except TaskQueueFullError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": {
                        "code": "TASK_QUEUE_FULL",
                        "message": "任务队列已满，请稍后重试",
                    }
                },
            )

        status_url = f"/v1/tasks/{task_id}"
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"task_id": task_id, "status_url": status_url},
            headers={"Location": status_url},
        )

    @app.post("/v1/index-tasks", status_code=status.HTTP_202_ACCEPTED)
    async def create_index_task(
        payload: CreateIndexTaskRequest,
        request: Request,
    ) -> JSONResponse:
        manager = request.app.state.task_manager
        operation = partial(
            build_index_task,
            payload=payload,
            embedder=request.app.state.embedder,
            index_build_slots=request.app.state.index_build_slots,
        )
        try:
            task_id = manager.submit(operation)
        except TaskQueueFullError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": {
                        "code": "TASK_QUEUE_FULL",
                        "message": "任务队列已满，请稍后重试",
                    }
                },
            )

        status_url = f"/v1/tasks/{task_id}"
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"task_id": task_id, "status_url": status_url},
            headers={"Location": status_url},
        )

    return app


app = create_app()  # 测试时注入 FakeEmbedder, app 供 Uvicorn 启动服务使用
