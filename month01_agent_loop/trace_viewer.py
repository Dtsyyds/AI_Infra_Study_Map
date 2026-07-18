"""
trace_view.py

Agent Trace 查看工具

功能：
1. 查看最近 N 条 Agent 执行记录
2. 查看最近一条 trace 的详细执行过程
3. 用于学习 Agent 行为复盘和后续 LLMOps 分析
"""

import argparse
import json
import os
from typing import Any, Dict, List

LOG_PATH = os.path.join(
    os.path.dirname(__file__),
    "logs",
    "agent_trace.jsonl"
)

def load_traces(log_path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """
    从 jsonl 日志文件中读取所有 trace
    """
    if not os.path.exists(log_path):
        return []
    
    traces = []

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                # 跳过循环行，避免影响后续日志解析
                continue
    return traces

def get_steps(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    获取 trace 的所有步骤信息
    """
    return trace.get("steps", [])

def print_summary(traces: List[Dict[str, Any]], last: int = 5) -> None:
    """
    打印最近 N 条 trace 摘要
    """
    if not traces:
        print("没有找到 trace 记录")
        return
    
    recent_traces = traces[-last:]
    print(f"最近 {len(recent_traces)} 条 Agent Trace:")
    for index, trace in enumerate(reversed(recent_traces), start=1):
        trace_id = trace.get("trace_id", "")
        start_time = trace.get("start_time", "")
        end_time = trace.get("end_time", "")
        user_input = trace.get("user_input", "")
        final_answer = trace.get("final_answer", "")
        duration = trace.get("duration_seconds", None)
        status = trace.get("status", "")

        steps = get_steps(trace)

        print(f"{index}. trace_id: {trace_id}")
        print(f"   start_time: {start_time}")
        print(f"   end_time: {end_time}")
        print(f"   status: {status}")
        print(f"   steps: {len(steps)}")

        if duration is not None:
            print(f"   duration_seconds: {duration}")

        print(f"   user_input: {user_input}")
        print(f"   final_answer: {final_answer}")
        print("-" * 80)

def print_detail(trace: Dict[str, Any]):
    """
    打印单条 trace 的详细执行记录
    """
    trace_id = trace.get("trace_id", "")
    start_time = trace.get("start_time", "")
    end_time = trace.get("end_time", "")
    user_input = trace.get("user_input", "")
    final_answer = trace.get("final_answer", "")
    duration = trace.get("duration_seconds", None)
    status = trace.get("status", "")
    error = trace.get("error", None)

    steps = get_steps(trace)

    print(f"Trace ID: {trace_id}")
    print(f"Start Time: {start_time}")
    print(f"End Time: {end_time}")

    if duration is not None:
        print(f"Duration Seconds: {duration}")

    print(f"status: {status}")
    print(f"User Input: {user_input}")
    print(f"Final Answer: {final_answer}")

    if error:
        print(f"Error: {error}")
    print("-" * 80)

    for index, step in enumerate(steps):
        step_index = step.get("step_index", "")
        time = step.get("time", "")
        llm_output = step.get("llm_output", "")
        action = step.get("action", "")
        result = step.get("result", {})

        print(f"Step {step_index}")
        print("-" * 80)
        print(f"time: {time}")

        print("\n[LLM Output]")
        print(llm_output)

        print("\n[Action]")
        print(action)

        print("\n[Result]")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        print("=" * 80)

def main() -> None:
    parser = argparse.ArgumentParser(description="查看 Agent Trace 日志")

    parser.add_argument("--last", type=int, default=5, help="查看最近 N 条记录，默认为 5")

    parser.add_argument("--detail", action="store_true", help="查看最近一条 trace 详细执行记录")

    args = parser.parse_args()

    traces = load_traces()

    if not traces:
        print("没有找到 trace 记录")
        print(f"请确保日志文件存在，路径为: {LOG_PATH}")
        return
    
    if args.detail:
        recent_traces = traces[-1:]
        for trace in recent_traces:
            print_detail(trace)
    else:
        print_summary(traces, last=args.last)

if __name__ == "__main__":
    main()