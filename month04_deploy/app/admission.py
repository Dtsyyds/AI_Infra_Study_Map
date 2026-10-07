import asyncio

from .request_context import RequestContext
from .metrics import ServiceMetrics

class AdmissionController:
    def __init__(
            self,
            max_running: int = 2,
            max_waiting: int = 3,
            *,
            metrics: ServiceMetrics,
    ) -> None:
        if max_running <= 0:
            raise ValueError("max_running must be positive")

        if max_waiting < 0:
            raise ValueError("max_waiting must be non-negative")

        self.execution_semaphore = asyncio.BoundedSemaphore(
            max_running
        )

        self.queue_semaphore = asyncio.BoundedSemaphore(
            max_waiting
        )
        self.metrics = metrics
    async def try_admit(
        self,
        ctx: RequestContext,
    ) -> bool:
        """
        对尚未终结的新请求调用一次。

        True：已获得执行名额或排队名额。
        False：容量已满，没有获得任何名额。
        """
        if ctx.permit_acquired or ctx.queue_slot_acquired:
            raise RuntimeError("request already holds capacity")

        if (
            ctx.cancel_event.is_set()
            or ctx.finalized_event.is_set()
        ):
            raise RuntimeError("request is already terminating")

        ctx.execution_semaphore = self.execution_semaphore
        ctx.queue_semaphore = self.queue_semaphore
        ctx.capacity_releaser = self.release_capacity

        # 有执行容量时直接进入，不占用排队名额
        if not self.execution_semaphore.locked():
            await self.execution_semaphore.acquire()
            ctx.permit_acquired = True
            self.metrics.execution_acquired()
            return True

        # 执行容量已满，再尝试获得排队名额
        if not self.queue_semaphore.locked():
            await self.queue_semaphore.acquire()
            ctx.queue_slot_acquired = True
            self.metrics.queue_entered()
            return True

        return False

    async def wait_for_execution(
        self,
        ctx: RequestContext,
    ) -> None:
        if ctx.permit_acquired:
            return

        if not ctx.queue_slot_acquired:
            raise RuntimeError("request was not admitted")

        try:
            await self.execution_semaphore.acquire()
            ctx.permit_acquired = True
            self.metrics.execution_acquired()

        finally:
            # 晋升失败或等待被取消，不再占用执行名额
            self.release_queue_slot(ctx)
            # # 晋升成功或等待被取消，都不再占用排队名额
            # if ctx.queue_slot_acquired:
            #     self.queue_semaphore.release()
            #     ctx.queue_slot_acquired = False

    def release_queue_slot(
        self,
        ctx: RequestContext,
    ) -> None:
        if not ctx.queue_slot_acquired:
            return

        self.queue_semaphore.release()
        ctx.queue_slot_acquired = False
        self.metrics.queue_left()


    def release_execution_permit(
        self,
        ctx: RequestContext,
    ) -> None:
        if not ctx.permit_acquired:
            return

        self.execution_semaphore.release()
        ctx.permit_acquired = False
        self.metrics.execution_released()

    def release_capacity(
        self,
        ctx: RequestContext,
    ) -> None:
        # 顺序仍然是先退出等待，再释放执行名额。
        self.release_queue_slot(ctx)
        self.release_execution_permit(ctx)
