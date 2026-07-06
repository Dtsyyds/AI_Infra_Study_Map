"""
main.py

V0 规则版 Agent 的命令行入口

"""
from agent import RuleBaseAgent, LLMAgent

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

    agent = LLMAgent(max_steps=5)

    # print("V1 FakerLLM React Agent Loop")
    print("当前支持：")
    print("1. 计算 1 + 2 * 4")
    print("2. 写入 ./test.txt hello world")
    print("3. 读取 ./test.txt")
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

if __name__ == "__main__":
    main()
