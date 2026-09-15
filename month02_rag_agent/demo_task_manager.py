import asyncio

from month02_rag_agent.task_manager import TaskManager, TaskQueueFullError

def failing_operation():
    raise ValueError("模拟任务失败")

async def main():
    # 只用一个 worker，验证失败之后还能继续处理任务。
    manager = TaskManager(worker_count=1, queue_capacity=3)
    manager.start()

    try:
        task_ids = [
            manager.submit(lambda: "第一个任务完成"),
            manager.submit(failing_operation),
            manager.submit(lambda: "失败之后的任务也完成"),
        ]

        # 上面没有 await，worker 尚未得到执行机会。
        # 因此三个任务仍在等待队列中，此时队列已满。
        try:
            manager.submit(lambda: "不应被执行")
        except TaskQueueFullError:
            print("第四个任务：已拒绝")
        else:
            raise AssertionError("队列已满，却接收了第四个任务")

        # 仅在这个演示中检查内部记录，验证入队失败后的回滚。
        assert len(manager._tasks) == 3, "被拒绝的任务留下了记录"
    finally:
        await manager.close()

    for task_id in task_ids:
        record = manager.get_task(task_id)
        print(record.status, record.result, record.error)


if __name__ == "__main__":
    asyncio.run(main())