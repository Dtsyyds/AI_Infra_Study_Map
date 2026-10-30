import argparse
import os
from typing import Any
from uuid import UUID
from datetime import datetime
from .task_store import TaskStore
from .retry_policy import RetryDecision


def read_input(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("text")

    if not isinstance(text, str) or not 1 <= len(text) <= 10_000:
        raise ValueError("payload.text 必须是长度为 1～10000 的字符串")

    return {"text": text}


def build_echo(state: dict[str, Any]) -> dict[str, Any]:
    return {"text": f"echo:{state['text']}"}


def format_result(state: dict[str, Any]) -> dict[str, Any]:
    return {"answer": state["text"]}


STEPS = (
    ("read_input_v1", read_input),
    ("build_echo_v1", build_echo),
    ("format_result_v1", format_result),
)

def restore_progress(
    payload: dict[str, Any],
    completed_steps: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """校验已保存步骤，返回下一步编号及其输入。"""

    if len(completed_steps) > len(STEPS):
        raise ValueError("保存的步骤数量超过当前流程")

    for expected_index, saved_step in enumerate(completed_steps):
        # 必须是 0、1、2……的连续前缀。
        if saved_step["step_index"] != expected_index:
            raise ValueError("步骤编号必须从 0 开始连续排列")

        expected_name = STEPS[expected_index][0]

        # 防止旧流程的记录被当前流程错误解释。
        if saved_step["step_name"] != expected_name:
            raise ValueError("保存的步骤名称与当前流程不匹配")

        if not isinstance(saved_step["result"], dict):
            raise ValueError("当前流程的步骤结果必须是字典")

    if not completed_steps:
        next_step = 0
        state = payload
    else:
        next_step = len(completed_steps)
        state = completed_steps[-1]["result"]

    return next_step, state


def run_task_once(
    store: TaskStore,
    task_id: UUID,
    *,
    crash_after_step: int | None = None,
    resume: bool = False,
) -> dict[str, Any] | None:
    if (
        crash_after_step is not None
        and crash_after_step not in range(len(STEPS))
    ):
        raise ValueError("crash_after_step 超出步骤范围")

    if resume:
        # 人工恢复模式：
        # 调用者必须事先确认旧 Worker 已退出，
        # 并保证没有其他 Worker 同时执行该任务。
        task = store.get_task(task_id)

        if task is None or task["status"] != "running":
            return None

        attempt = store.get_running_attempt(task_id)
        if attempt is None:
            raise RuntimeError(
                "无法恢复：running Task 缺少对应的 running Attempt"
            )
    else:
        # task = store.try_start_task(task_id)

        # if task is None:
        #     return None
        claimed = store.try_start_task_with_attempt(task_id)
        if claimed is None:
            return None
        task, attempt = claimed

    return _execute_claimed_task(
        store,
        task,
        attempt,
        restore_from_steps=resume,
        crash_after_step=crash_after_step,
    )

    # if task["task_type"] != "demo.echo":
    #     raise ValueError(
    #         f"不支持的任务类型: {task['task_type']}"
    #     )

    # if resume:
    #     completed_steps = store.get_completed_steps(task_id)

    #     # TODO 1：
    #     # 调用 restore_progress()。
    #     # 参数是 task["payload"] 和 completed_steps。
    #     # 将两个返回值分别赋给 next_step、state。
    #     # raise NotImplementedError("完成 TODO 1")
    #     next_step, state = restore_progress(
    #         task["payload"], completed_steps)

    #     print(
    #         f"RESTORED next_step={next_step}",
    #         flush=True,
    #     )
    # else:
    #     next_step = 0
    #     state = task["payload"]

    # # TODO 2：
    # # 从 STEPS 中取出 next_step 开始的剩余步骤，
    # # 赋给 remaining_steps。
    # # 提示：使用切片。
    # # raise NotImplementedError("完成 TODO 2")
    # remaining_steps = STEPS[next_step:]

    # for step_index, (step_name, operation) in enumerate(
    #     remaining_steps,
    #     start=next_step,
    # ):
    #     # state = operation(state)
    #     try:
    #         state = operation(state)
    #     except ValueError as exc:
    #         error = {
    #             "type": type(exc).__name__,
    #             "message": str(exc),
    #             "step_index": step_index,
    #             "step_name": step_name,
    #         }
    #         # TODO 1：
    #         # 调用 store.try_fail_task_with_attempt()。
    #         # 参数：task_id、attempt["attempt_id"]、error。
    #         # 返回值赋给 failed。
    #         failed = store.try_fail_task_with_attempt(
    #             task_id, attempt["attempt_id"], error)

    #         # TODO 2：
    #         # 如果 failed 为 None，说明失败记录没有被接受。
    #         # 抛出 RuntimeError("failure recording rejected") from exc。
    #         if failed is None:
    #             raise RuntimeError(
    #                 "failure recording rejected") from exc
    #         # 完成以上两项后，删除下面这个占位异常。

    #         # 失败记录提交成功后，继续向调用方抛出原始 ValueError。
    #         raise


    #     store.save_completed_step(
    #         task_id,
    #         step_index,
    #         step_name,
    #         state,
    #     )

    #     print(
    #         f"STEP_SAVED index={step_index} name={step_name}",
    #         flush=True,
    #     )

    #     if crash_after_step == step_index:
    #         print(
    #             f"INJECTED_CRASH after_step={step_index}",
    #             flush=True,
    #         )
    #         os._exit(86)

    # # finished = store.try_succeed_task(task_id, state)
    # finished = store.try_succeed_task_with_attempt(
    #     task_id, attempt["attempt_id"], state)

    # if finished is None:
    #     raise RuntimeError(
    #         "completion rejected: task state no longer matches"
    #     )

    # return finished

def _execute_claimed_task(
    store: TaskStore,
    task: dict[str, Any],
    attempt: dict[str, Any],
    *,
    restore_from_steps: bool,
    crash_after_step: int | None = None,
) -> dict[str, Any]:
    task_id = task["task_id"]

    # TODO 1：
    # 将原 run_task_once 中，从 task_type 校验开始，
    # 到最后 return finished 为止的执行代码移到这里。
    if task["task_type"] != "demo.echo":
        raise ValueError(
            f"不支持的任务类型: {task['task_type']}"
        )
    # True：读取步骤并调用 restore_progress()。
    # False：next_step = 0，state = task["payload"]。
    if restore_from_steps:
        completed_steps = store.get_completed_steps(task_id)

        # TODO 1：
        # 调用 restore_progress()。
        # 参数是 task["payload"] 和 completed_steps。
        # 将两个返回值分别赋给 next_step、state。
        # raise NotImplementedError("完成 TODO 1")
        next_step, state = restore_progress(
            task["payload"], completed_steps)

        print(
            f"RESTORED next_step={next_step}",
            flush=True,
        )
    else:
        next_step = 0
        state = task["payload"]

    # TODO 2：
    # 从 STEPS 中取出 next_step 开始的剩余步骤，
    # 赋给 remaining_steps。
    # 提示：使用切片。
    # raise NotImplementedError("完成 TODO 2")
    remaining_steps = STEPS[next_step:]

    for step_index, (step_name, operation) in enumerate(
        remaining_steps,
        start=next_step,
    ):
        # state = operation(state)
        try:
            state = operation(state)
        except ValueError as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "step_index": step_index,
                "step_name": step_name,
            }
            # TODO 1：
            # 调用 store.try_fail_task_with_attempt()。
            # 参数：task_id、attempt["attempt_id"]、error。
            # 返回值赋给 failed。
            failed = store.try_fail_task_with_attempt(
                task_id, attempt["attempt_id"], error)

            # TODO 2：
            # 如果 failed 为 None，说明失败记录没有被接受。
            # 抛出 RuntimeError("failure recording rejected") from exc。
            if failed is None:
                raise RuntimeError(
                    "failure recording rejected") from exc
            # 完成以上两项后，删除下面这个占位异常。

            # 失败记录提交成功后，继续向调用方抛出原始 ValueError。
            raise


        store.save_completed_step(
            task_id,
            step_index,
            step_name,
            state,
        )

        print(
            f"STEP_SAVED index={step_index} name={step_name}",
            flush=True,
        )

        if crash_after_step == step_index:
            print(
                f"INJECTED_CRASH after_step={step_index}",
                flush=True,
            )
            os._exit(86)

    # finished = store.try_succeed_task(task_id, state)
    finished = store.try_succeed_task_with_attempt(
        task_id, attempt["attempt_id"], state)

    if finished is None:
        raise RuntimeError(
            "completion rejected: task state no longer matches"
        )

    return finished

    # TODO 2：
    # 将迁移过来的检查点分支：
    #     if resume:
    # 改为：
    #     if restore_from_steps:
    #
    # True：读取步骤并调用 restore_progress()。
    # False：next_step = 0，state = task["payload"]。

    # raise NotImplementedError("完成执行逻辑迁移后删除")

def retry_task_once(
    store: TaskStore,
    task_id: UUID,
    expected_attempt_id: UUID,
    *,
    retryable_error: bool,
    outcome_unknown: bool,
    replay_safe: bool,
    crash_after_step: int | None = None,
) -> dict[str, Any] | RetryDecision | None:
    """领取一次重试，并从已保存的有效步骤继续执行。"""

    # 参数校验必须放在领取前，避免参数错误后留下 running 任务。
    if (
        crash_after_step is not None
        and crash_after_step not in range(len(STEPS))
    ):
        raise ValueError("crash_after_step 超出步骤范围")

    claimed = store.try_retry_task_with_attempt(
        task_id,
        expected_attempt_id,
        retryable_error=retryable_error,
        outcome_unknown=outcome_unknown,
        replay_safe=replay_safe,
    )

    # TODO 1：
    # 如果 claimed 是 None、RetryDecision.STOP
    # 或 RetryDecision.RECONCILE，直接返回 claimed。
    if claimed is None or claimed is RetryDecision.STOP or claimed is RetryDecision.RECONCILE:
        return claimed

    # TODO 2：
    # 将成功领取的结果拆包为 task、attempt。
    task, attempt = claimed

    # TODO 3：
    # 调用并返回 _execute_claimed_task() 的结果。
    # 传入 store、task、attempt；
    # restore_from_steps=True；
    # crash_after_step 使用传入参数。
    return _execute_claimed_task(
        store=store,
        task=task,
        attempt=attempt,
        restore_from_steps=True,
        crash_after_step=crash_after_step,
    )

def main() -> int:
    parser = argparse.ArgumentParser(
        description="执行一个已持久化的 demo.echo 任务"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="人工恢复 running 任务；要求旧 Worker 已退出且无并发执行",
    )
    parser.add_argument(
        "--crash-after-step",
        type=int,
        choices=range(len(STEPS)),
        default=None,
        help="仅用于故障实验：指定步骤提交后立即退出",
    )
    parser.add_argument(
        "--task-id",
        required=True,
        type=UUID,
    )
    args = parser.parse_args()

    dsn = os.environ.get("AGENT_DATABASE_URL")
    if not dsn:
        parser.error("缺少 AGENT_DATABASE_URL 环境变量")

    store = TaskStore(dsn)

    print(
        f"WORKER pid={os.getpid()} task_id={args.task_id}",
        flush=True,
    )

    finished = run_task_once(store, args.task_id, crash_after_step=args.crash_after_step, resume=args.resume)

    if finished is None:
        print(
            "NOT_ELIGIBLE: task missing or status is not "
            "eligible for the selected mode",
            flush=True,
        )
        return 2

    print(
        f"SUCCEEDED result={finished['result']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())