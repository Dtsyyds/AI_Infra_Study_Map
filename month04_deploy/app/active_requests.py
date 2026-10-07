import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from .metrics import ServiceMetrics

class ActiveRequestLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_active: int = 16,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        if max_active <= 0:
            raise ValueError("max_active must be positive")

        self.app = app
        self.metrics = metrics
        self.slots = asyncio.BoundedSemaphore(max_active)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        protected = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] in {
                "/v1/agent/run",
                "/v1/agent/stream",
            }
        )

        if not protected:
            await self.app(scope, receive, send)
            return

        loop = asyncio.get_running_loop()
        started_at = loop.time()

        try:

            # 活跃请求容量满时立即拒绝，不再额外排队
            if self.slots.locked():
                self.metrics.record_admission_rejection(
                    "active_limit"
                )

                response = JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "code": "active_request_limit",
                        }
                    },
                )
                await response(scope, receive, send)
                return

            # 同一事件循环内，可用时acquire立即完成
            await self.slots.acquire()
            self.metrics.active_request_entered()

            try:
                # 覆盖路由执行、响应发送及下游finally清理
                await self.app(scope, receive, send)
            finally:
                self.slots.release()
                self.metrics.active_request_left()

        finally:
            finished_at = loop.time()
            self.metrics.observe_http_request_duration(
                started_at=started_at,
                finished_at=finished_at,
            )