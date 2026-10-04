import asyncio

import pytest

from month04_deploy.app.admission import AdmissionController
from month04_deploy.app.request_context import (
    FinalizeEvent,
    RequestContext,
)
from month04_deploy.app.request_lifecycle import (
    cleanup_request_resources,
    finalize_request,
)


def make_context(request_id: str) -> RequestContext:
    return RequestContext(
        request_id=request_id,
        deadline=asyncio.get_running_loop().time() + 30,
    )


@pytest.mark.asyncio
async def test_capacity_limit_and_queue_promotion():
    controller = AdmissionController(
        max_running=1,
        max_waiting=1,
    )

    first = make_context("first")
    second = make_context("second")
    third = make_context("third")

    waiting_task = None

    try:
        # 第一个请求直接获得执行Permit
        assert await controller.try_admit(first) is True
        assert first.permit_acquired is True
        assert first.queue_slot_acquired is False

        # 第二个请求获得排队名额
        assert await controller.try_admit(second) is True
        assert second.permit_acquired is False
        assert second.queue_slot_acquired is True

        # 第三个请求立即被拒绝
        assert await controller.try_admit(third) is False
        assert third.permit_acquired is False
        assert third.queue_slot_acquired is False

        waiting_task = asyncio.create_task(
            controller.wait_for_execution(second)
        )
        second.background_task = waiting_task

        await asyncio.sleep(0)

        assert waiting_task.done() is False

        # 第一个请求结束，生命周期清理释放执行Permit
        await finalize_request(
            first,
            FinalizeEvent.COMPLETED,
            cleanup_request_resources,
        )

        await asyncio.wait_for(waiting_task, timeout=1)

        # 第二个请求晋升：持有执行Permit，释放排队名额
        assert second.permit_acquired is True
        assert second.queue_slot_acquired is False

        # 用新请求验证排队名额确实可再次使用
        fourth = make_context("fourth")
        assert await controller.try_admit(fourth) is True
        assert fourth.queue_slot_acquired is True
        assert fourth.permit_acquired is False

        await finalize_request(
            fourth,
            FinalizeEvent.CLIENT_DISCONNECTED,
            cleanup_request_resources,
        )

    finally:
        # 测试退出时回收任务和已有资源
        for ctx in (first, second):
            await finalize_request(
                ctx,
                FinalizeEvent.CLIENT_DISCONNECTED,
                cleanup_request_resources,
            )

        if waiting_task is not None:
            await asyncio.gather(
                waiting_task,
                return_exceptions=True,
            )

@pytest.mark.asyncio
async def test_cancelled_waiter_releases_queue_slot():
    controller = AdmissionController(
        max_running=1,
        max_waiting=1,
    )

    first = make_context("first")
    second = make_context("second")
    third = make_context("third")

    waiting_task = None

    try:
        # first占用执行Permit
        assert await controller.try_admit(first) is True
        assert first.permit_acquired is True
        assert first.queue_slot_acquired is False

        # second占用排队名额
        assert await controller.try_admit(second) is True
        assert second.permit_acquired is False
        assert second.queue_slot_acquired is True

        waiting_task = asyncio.create_task(
            controller.wait_for_execution(second)
        )
        second.background_task = waiting_task

        await asyncio.sleep(0)
        assert waiting_task.done() is False

        # 只取消second，first仍应持有执行Permit
        claimed = await finalize_request(
            second,
            FinalizeEvent.CLIENT_DISCONNECTED,
            cleanup_request_resources,
        )

        assert claimed is True
        assert waiting_task.cancelled() is True
        assert second.queue_slot_acquired is False
        assert second.permit_acquired is False

        assert first.permit_acquired is True

        # 排队名额已经恢复，但执行Permit仍被first占用
        assert await controller.try_admit(third) is True
        assert third.queue_slot_acquired is True
        assert third.permit_acquired is False

    finally:
        # 完成业务断言后，再清理所有测试请求
        for ctx in (first, second, third):
            await finalize_request(
                ctx,
                FinalizeEvent.CLIENT_DISCONNECTED,
                cleanup_request_resources,
            )

        if waiting_task is not None:
            await asyncio.gather(
                waiting_task,
                return_exceptions=True,
            )