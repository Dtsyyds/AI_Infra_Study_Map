"""
main.py

V0 规则版 Agent 的命令行入口

"""

from concurrent.futures import ThreadPoolExecutor

from agent import LLMAgent
from tool_runtime import ToolRuntime


def run_cli(agent: LLMAgent) -> None:
    """运行命令行交互循环。"""
    print("V1 FakerLLM React Agent Loop")
    print("当前支持：")
    print("1. 计算 1 + 2 * 4")
    print("2. 写入 ./test.txt hello world")
    print("3. 读取 ./test.txt")
    print("4. 查看当前目录下的文件")
    print("输入 exit / quit 退出")
    print("输入 memory 查看记忆")
    print("输入 clear 清空记忆")

    while True:
        user_input = input("\nuser: ").strip()

        if user_input.lower() in ["exit", "quit"]:
            print("Byebye~")
            break

        if user_input.lower() == "memory":
            print("\n[Memory]")
            print(agent.show_memory())
            continue
        elif user_input.lower() == "clear":
            agent.clear_memory()
            print("Memory cleared.")
            continue

        answer = agent.run(user_input)
        print("\nAgent:", answer)


def main():
    # agent = RuleBaseAgent()

    # print("V0 规则版 Agent Loop")
    # print("当前支持：")
    # print("1. 计算 1 + 2 * 4")
    # print("2. 写入 ./test.txt hello world")
    # print("3. 读取 ./test.txt")
    # print("输入 exit / quit 退出")
    # print("输入 memory 查看记忆")
    # print("输入 clear 清空记忆")

    executor = ThreadPoolExecutor(max_workers=4)
    tool_runtime = ToolRuntime(executor)

    agent = LLMAgent(max_steps=5, tool_runtime=tool_runtime, step_timeout_seconds=10)

    try:
        run_cli(agent)
    finally:
        executor.shutdown(
            wait=True,
            cancel_futures=True,  # cancel_futures=True：取消队列中尚未开始的任务；
        )


if __name__ == "__main__":
    main()
