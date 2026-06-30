# Agent 最小架构 就是一个带工具的 while 循环
# 本质就是 构造 prompt 然后调用 LLM 然后解析 LLM 的输出 然后调用工具函数 然后返回结果 循环直到结束
`
while step < max_steps:
    prompt = build_prompt(user_input, history, tools)       # 根据用户的提示词，对话历史和工具构建 prompt
    llm_output = call_llm(prompt)       # 调用 LLM 生成响应
    action = parse_action(llm_output)   # 解析 LLM 的输出，得到 action

    # 调用工具函数，根据 action 执行相应的操作
    if action is Finish:
        return final_answer
    
    observation = run_tool(action)
    history.append(llm.output)
    histroy.append(observation)
`