"""
trace_stats.py

Agent Trace 统计分析工具

作用：
1. 统计 Agent 总任务数、成功率、平均 step 数、平均耗时等
2. 统计工具调用情况
3. 统计工具错误次数
4. 为后续 Agent Eval / AgentOps 做准备
"""

import os
import json
import re
from typing import List, Dict, Any
from collections import Counter

LOG_PATH = os.path.join(
    os.path.dirname(__file__),
    "logs",
    "agent_trace.jsonl"
)

def load_traces(log_path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """
    从 jsonl 日志文件中读取所有的 traces
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
                continue

    return traces

def get_steps(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "steps" in trace:
        return trace["steps"]
    else:
        return []

def extract_tool_name(action: str) -> str:
    """
    从 action 中提取工具名
    """
    if not action:
        return "unknown"
    
    action = action.strip()
    action = action.replace("：", ":")

    if action.startswith("Action:"):
        action_text = action[len("Action:"):].strip()
    else:
        action_text = action

    if action_text.startswith("Finish["):
        return "Finish"
    
    match = re.match(r"(\w+)\(", action_text)

    if match:
        return match.group(1)
    else:
        return "unknown"

def is_tool_error(result: Dict[str, Any]) -> bool:
    """
    判断结果是否为工具错误
    """
    if not isinstance(result, dict):
        return False
    
    content = result.get("content", "")
    if isinstance(content, str) and content.startswith("TOOL_ERROR:"):
        return True
    
    success = result.get("success", None)

    if success is False:
        return True
    
    return False

def analyze_traces(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    统计 trace 数据
    """
    total_tasks = len(traces)
    status_counter = Counter()
    tool_counter = Counter()
    tool_error_counter = Counter()

    total_steps = 0
    duration_values = []

    error_traces = []

    for trace in traces:
        status = trace.get("status", "unknown")
        status_counter[status] += 1

        steps = get_steps(trace)
        total_steps += len(steps)

        duration = trace.get("duration_second", None)

        if isinstance(duration, (int, float)):
            duration_values.append(duration)

        for step in steps:
            action = step.get("action", "")
            result = step.get("result", {})

            tool_name = extract_tool_name(action)

            if tool_name != "Finish":
                tool_counter[tool_name] += 1

            if is_tool_error(result):
                tool_error_counter[tool_name] += 1

        if status != "success":
            error_traces.append(trace)

    success_count = status_counter.get("success", 0)
    success_rate = 0.0
    if total_tasks > 0:
        success_rate = success_count / total_tasks * 100
    
    avg_steps = 0.0

    if duration_values:
        avg_duration = sum(duration_values) / len(duration_values)
    else:
        avg_duration = 0.0

    if total_steps > 0:
        avg_steps = total_steps / total_tasks

    return {
        "total_tasks": total_tasks,
        "status_counter": status_counter,
        "success_count": success_count,
        "success_rate": success_rate,
        "avg_steps": avg_steps,
        "avg_duration": avg_duration,
        "tool_counter": tool_counter,
        "tool_error_counter": tool_error_counter,
        "error_traces": error_traces,
    }

def print_stats(stats: Dict[str, Any]) -> None:
    """
    打印统计结果。
    """
    print("Agent Trace Statistics")
    print("=" * 80)

    print(f"总任务数: {stats['total_tasks']}")
    print(f"成功任务数: {stats['success_count']}")
    print(f"成功率: {stats['success_rate']:.2f}%")
    print(f"平均 Step 数: {stats['avg_steps']:.2f}")
    print(f"平均耗时: {stats['avg_duration']:.3f} 秒")

    print("\n状态统计:")
    for status, count in stats["status_counter"].most_common():
        print(f"- {status}: {count}")

    print("\n工具调用统计:")
    if stats["tool_counter"]:
        for tool_name, count in stats["tool_counter"].most_common():
            print(f"- {tool_name}: {count} 次")
    else:
        print("- 暂无工具调用记录")

    print("\n工具错误统计:")
    if stats["tool_error_counter"]:
        for tool_name, count in stats["tool_error_counter"].most_common():
            print(f"- {tool_name}: {count} 次")
    else:
        print("- 暂无工具错误")

    print("\n失败 Trace:")
    if stats["error_traces"]:
        for trace in stats["error_traces"][-5:]:
            trace_id = trace.get("trace_id", "")
            user_input = trace.get("user_input", "")
            status = trace.get("status", "")
            final_answer = trace.get("final_answer", "")

            print("-" * 80)
            print(f"trace_id: {trace_id}")
            print(f"status: {status}")
            print(f"user_input: {user_input}")
            print(f"final_answer: {final_answer}")
    else:
        print("- 暂无失败 Trace")


def main() -> None:
    traces = load_traces()

    if not traces:
        print("没有找到 trace 日志。")
        print(f"日志路径: {LOG_PATH}")
        return

    stats = analyze_traces(traces)
    print_stats(stats)

if __name__ == "__main__":
    main()

