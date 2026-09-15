import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import uuid4

TaskStatus = Literal["queued", "running", "succeeded", "failed"]
Operation = Callable[[], Any]

@dataclass(slots=True)
class TaskRecord:
    task_id: str
    status: TaskStatus = "queued"
    result: Any = None
    error: str | None = None


class TaskQueueFullError(RuntimeError):
    """等待队列已满，拒绝新任务。"""


class TaskManager:
    def __init__(
            self,
            *,
            worker_count: int = 2,
            queue_capacity: int = 3,
    ):
        if worker_count < 1 or queue_capacity < 1:
            raise ValueError("worker_count 和 queue_capacity 必须大于 0")

        self._worker_count = worker_count
        # 使用 asyncio.Queue 管理等待任务；它应当在同一个事件循环中使用，不能让工作线程直接操作队列或任务表。
        self._queue: asyncio.Queue[tuple[str, Operation]] = asyncio.Queue(maxsize=queue_capacity)
        self._tasks: dict[str, TaskRecord] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._accepting = False

    def start(self):
        if self._workers:
            return  # 已经启动

        self._workers = [
            asyncio.create_task(self._worker())
            for _ in range(self._worker_count)
        ]

        self._accepting = True

    def get_task(self, task_id: str) -> TaskRecord:
        # 返回浅拷贝，避免调用方直接修改内部记录的状态字段。
        return replace(self._tasks[task_id])

    async def close(self) -> None:
        """停止接收新任务，等待已接收任务处理完毕。"""
        self._accepting = False
        await self._queue.join()

        # 此时任务已经处理完，再取消等待取任务的 worker。
        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def submit(self, operation: Operation) -> str:
        if not self._accepting:
            raise RuntimeError("TaskManager 尚未启动或正在关闭")

        task_id = uuid4().hex
        record = TaskRecord(task_id=task_id)

        # 登记记录、尝试入队、处理队列满、返回 ID。
        self._tasks[task_id] = record
        try:
            # 队列满时会抛出 asyncio.QueueFull。这里不用 await put()，因为我们的约定是满了立即拒绝，不让提交者继续等待
            self._queue.put_nowait((task_id, operation))
        except asyncio.QueueFull as exc:
            del self._tasks[task_id]  # 回滚登记
            raise TaskQueueFullError("任务队列已满，无法提交新任务") from exc
        return task_id

    async def _worker(self) -> None:
        # 持续取任务、执行、记录结果、完成队列记账。
        while True:
            task_id, operation = await self._queue.get()
            self._tasks[task_id].status = "running"
            try:
                result = await asyncio.to_thread(operation)
                self._tasks[task_id].status = "succeeded"
                self._tasks[task_id].result = result
            except Exception as e:
                self._tasks[task_id].status = "failed"
                self._tasks[task_id].error = str(e)
            finally:
                self._queue.task_done()
