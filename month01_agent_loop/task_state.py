from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS = {
    TaskStatus.QUEUED: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.QUEUED,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def validate_task_transition(
    current: TaskStatus,
    target: TaskStatus,
) -> None:
    """
    检查一次任务状态转换是否合法。

    合法：正常返回 None。
    非法：抛出 ValueError。
    """

    # TODO：
    # 1. 从 ALLOWED_TRANSITIONS 获取 current 允许到达的状态。
    if current not in ALLOWED_TRANSITIONS:
        raise ValueError(f"未知的当前状态: {current}")

    allowed = ALLOWED_TRANSITIONS[current]
    
    # 2. 检查 target 是否属于这些状态。
    if target not in allowed:
    # 3. 如果不属于，抛出 ValueError；
        raise ValueError(
            f"状态转换非法: {current.value} -> {target.value}"
        )
    #    错误信息中包含当前状态和目标状态。
    #
    # 提示：
    # 三个终态对应空集合，因此没有合法的后续转换。

    return None


def main() -> None:
    cases = [
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.QUEUED, TaskStatus.SUCCEEDED),
        (TaskStatus.SUCCEEDED, TaskStatus.FAILED),
    ]

    for current, target in cases:
        try:
            validate_task_transition(current, target)
        except ValueError:
            print(
                f"{current.value} -> {target.value}: rejected"
            )
        else:
            print(
                f"{current.value} -> {target.value}: allowed"
            )


if __name__ == "__main__":
    main()