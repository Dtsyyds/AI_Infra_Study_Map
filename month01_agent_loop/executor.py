"""
executor.py

这个文件负责连接 parser.py 和 tools.py。

流程：
1. 接收一条 Action 指令
2. 使用 parse_action 解析
3. 如果是工具调用，就执行对应工具
4. 如果是 Finish，就返回最终答案
5. 如果解析失败，就返回错误信息
"""

from execution_context import ExecutionContext, TaskCancelledError, TaskTimeoutError
from parser import parse_action
from tool_runtime import ToolExecutionTimeoutError, ToolRuntime
from tools import run_tool


def execute_action(
    action_text: str,
    *,
    context: ExecutionContext | None = None,
    tool_runtime: ToolRuntime | None = None,
    step_timeout_seconds: float | None = None,
) -> dict:
    """
    执行一条 Action 指令。

    Args:
        action_text: 例如：
            Action: calculator(expression="1 + 2 * 4")
            Action: read_file(path="./test.txt")
            Action: write_file(path="./test.txt", content="hello world")
            Action: Finish[任务完成]

    Returns:
        {
            "type": "observation" | "finish" | "error",
            "content": "工具返回结果 / 最终答案 / 错误信息"
        }
    """
    # operation = lambda: run_tool(
    #     tool_name,
    #     **args,
    # )
    # if context is not None:
    #     try:
    #         context.check_active()
    #     except TaskCancelledError:
    #         return {
    #             "type": "cancelled",
    #             "content": "任务已取消",
    #             "success": False,
    #         }
    #     except TaskTimeoutError:
    #         return {
    #             "type": "timeout",
    #             "content": "任务已超过截止时间",
    #             "success": False,
    #         }

    # action = parse_action(action_text)

    # if action["type"] == "error":
    #     return {"type": "error", "content": action["content"]}

    # if action["type"] == "finish":
    #     return {"type": "finish", "content": action["content"]}

    # if action["type"] == "tool":
    #     tool_name = action["tool_name"]
    #     args = action["args"]

    #     if context is not None:
    #         effective_timeout = context.effective_timeout_seconds(
    #                             step_timeout_seconds=step_timeout_seconds,
    #                         )
    #         try:
    #             context.check_active()
    #         except TaskCancelledError:
    #             return {
    #                 "type": "cancelled",
    #                 "content": "任务已取消",
    #                 "success": False,
    #             }
    #         except TaskTimeoutError:
    #             return {
    #                 "type": "timeout",
    #                 "content": "任务已超过截止时间",
    #                 "success": False,
    #             }
    #     else:
    #         effective_timeout = step_timeout_seconds
    #     if tool_runtime is not None:
    #         try:

    #             tool_runtime.run(operation, timeout_seconds=effective_timeout)
    #         except ToolExecutionTimeoutError:
    #             return {
    #                 "type": "timeout",
    #                 "content": "工具执行超时",
    #                 "success": False,
    #             }

    #     observation = run_tool(tool_name, **args)

    #     # return {
    #     #     "type": "observation",
    #     #     "content": observation
    #     # }

    #     success = not observation.startswith("TOOL_ERROR:")

    #     return {"type": "observation", "content": observation, "success": success}

    # return {"type": "error", "content": f"未知解析类型: {action['type']}"}
    action = parse_action(action_text)

    if action["type"] == "error":
        return {"type": "error", "content": action["content"]}
    if action["type"] == "finish":
        return {"type": "finish", "content": action["content"]}
    if action["type"] == "tool":
        tool_name = action["tool_name"]
        args = action["args"]
        if context is not None:
            try:
                context.check_active()
            except TaskCancelledError:
                return {
                    "type": "cancelled",
                    "content": "任务已取消",
                    "success": False,
                }
            except TaskTimeoutError:
                return {
                    "type": "timeout",
                    "content": "任务已超过截止时间",
                    "success": False,
                }

            effective_timeout = context.effective_timeout_seconds(
                step_timeout_seconds=step_timeout_seconds,
            )
        else:
            effective_timeout = step_timeout_seconds

        def operation() -> str:
            return run_tool(
                tool_name,
                **args,
            )

        if tool_runtime is not None:
            try:
                observation = tool_runtime.run(operation, timeout_seconds=effective_timeout)
                # observation = operation() # 这里会执行两次
            except ToolExecutionTimeoutError:
                return {
                    "type": "timeout",
                    "content": "工具执行超时",
                    "success": False,
                }
        else:
            # observation = run_tool(action["tool_name"], **args)
            observation = operation()

        success = not observation.startswith("TOOL_ERROR:")

        return {"type": "observation", "content": observation, "success": success}

    return {"type": "error", "content": f"未知解析类型: {action['type']}"}


if __name__ == "__main__":
    test_cases = [
        'Action: calculator(expression="1 + 2 * 4")',
        'Action: write_file(path="./test.txt", content="hello world!")',
        'Action: read_file(path="./test.txt")',
        "Action: Finish[任务完成]",
        'Action: unknown_tool(value="test")',
        "hello world",
    ]

    for text in test_cases:
        print("=" * 60)
        print("输入:", text)
        result = execute_action(text)
        print("执行结果:", result)
