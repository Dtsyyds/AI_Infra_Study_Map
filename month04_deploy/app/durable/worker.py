import argparse
import os
from typing import Any
from uuid import UUID

from .task_store import TaskStore


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
    else:
        task = store.try_start_task(task_id)

        if task is None:
            return None

    if task["task_type"] != "demo.echo":
        raise ValueError(
            f"不支持的任务类型: {task['task_type']}"
        )

    if resume:
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
        state = operation(state)

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

    finished = store.try_succeed_task(task_id, state)

    if finished is None:
        raise RuntimeError(
            "completion rejected: task state no longer matches"
        )

    return finished


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