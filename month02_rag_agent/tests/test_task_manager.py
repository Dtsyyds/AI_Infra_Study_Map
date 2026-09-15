import asyncio

from month02_rag_agent.task_manager import TaskManager

def test_task_manager_records_success_and_failure():
    async def scenario():
        manager = TaskManager(worker_count=1, queue_capacity=2)
        manager.start()

        success_id = manager.submit(lambda: "任务完成")

        def failing_operation():
            raise ValueError("模拟任务失败")

        failure_id = manager.submit(failing_operation)

        await manager.close()

        success = manager.get_task(success_id)
        failure = manager.get_task(failure_id)

        assert success.status == "succeeded"
        assert success.result == "任务完成"
        assert success.error is None

        assert failure.status == "failed"
        assert failure.result is None
        assert failure.error == "模拟任务失败"

    asyncio.run(scenario())

