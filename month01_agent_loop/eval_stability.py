"""
eval_stability.py

Agent 多轮稳定性测评

功能：
1. 每个 Eval Case 重复运行多次
2. 统计总体和单用例通过率
3. 统计平均耗时、P50、P95
4. 统计工具调用路径
5. 汇总失败原因
6. 保存完整测评结果

result:
{
    "case_id": "read_missing_file",         # 测评用例 ID, 一个测评用例的稳定、唯一标识，多轮测试不变 case_id 只更新 run_index
    "run_index": 2,
    "passed": False,
    "duration_seconds": 35.6,
    "failure_type": "tool_selection_error",     # api_timeout   api_error   tool_selection_error    tool_execution_error    action_parse_error  repeated_action safety_violation    answer_mismatch trajectory_mismatch unknown
    "tool_path": ["read_file", "write_file"],   # tool_path 最好保存成有序列表,列表更容易检查顺序、统计和程序处理
    "user_input": "读取 ./not_exist.txt"
    "failure_detail": [
        "调用了禁止工具 write_file",
        "缺少期望工具 list_files"
    ],
}
"""

from __future__ import annotations

import argparse
import json 
import os
from  statistics import mean
from collections import Counter
from datetime import datetime
from typing import List, Dict, Any, Optional

from agent import LLMAgent

from eval import (
    EVAL_CASES_PATH,
    load_eval_cases,
    run_eval_case,
)

import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RESULT_DIR = os.path.join(BASE_DIR, "eval_results")

FAILURE_PRIORITY = [
    "safety_violation",
    "api_timeout",
    "api_error",
    "tool_selection_error",
    "tool_execution_error",
    "action_parse_error",
    "repeated_action",
    "answer_mismatch",
    "trajectory_mismatch",
    "unknown",
]

def percentile(values: List[float], ratio: float) -> float:
    """
    计算百分位数

    ratio:
        0.50 -> P50     # 典型任务耗时
        0.95 -> P95     # 慢请求耗时

    使用线性插值，不依赖 numpy
    """
    if not values:
        return 0.0
    sorted_values = sorted(values)  # 升序排列
    if len(sorted_values) == 1:
        return sorted_values[0]
    
    position = ratio * (len(sorted_values) - 1)

    lower_position = int(position)
    upper_position = min(lower_position + 1, len(sorted_values) - 1)

    weight = position - lower_position  
    # 根据索引取出对应的所记实际数值
    lower_value = sorted_values[lower_position]
    upper_value = sorted_values[upper_position]

    return (
        lower_value + weight * (upper_value - lower_value)
    )

def get_tool_path(result: Dict[str, Any]) -> str:
    """
    从评测结果中生成工具调用路径‘
    示例：
        calculator
        read_file -> list_files
        no tools
    """
    trace_summary = result.get("trace_summary", {})

    if not trace_summary:
        return "(no tools)"
    
    tool_calls = trace_summary.get("tool_calls", [])

    if not tool_calls:
        return "(no tools)"
    
    return " -> ".join(tool_calls)

def select_cases(cases: List[Dict[str, Any]], case_id: Optional[str]) -> List[Dict[str, Any]]:
    """
    按照 case_id 选择用例
    没有 case_id 时返回所有用例
    """
    if not case_id:
        return cases
    # 从 cases 列表中筛选出所有 case_id 字段等于外部变量 case_id 的字典，并将它们组成一个新列表 selected
    selected = [
        case
        for case in cases
        if case.get("id") == case_id
    ]

    if not selected:
        available_ids = [
            case.get("id", "unknown")
            for case in cases
        ]
        raise ValueError(
            f"case_id '{case_id}' not found. "
            f"Available ids: {available_ids}"
        )
    
    return selected

def run_stability_eval(cases: List[Dict[str, Any]], runs: int, max_steps: int) -> List[Dict[str, Any]]:
    """
    对每个评测用例重复执行指定次数
    每次执行都使用独立的 Agent 实例
    避免不同轮次之间的 Memory 污染
    """
    results = []

    total_runs = len(cases) * runs
    current_run = 0

    for case in cases:
        case_id = case.get("id", "unknown")
        user_input = case.get("input")

        for run_index in range(1, runs + 1):
            current_run += 1

            print("\n" + "=" * 80)
            print(
                f"Stability Run "
                f"{current_run}/{total_runs}"
            )
            print(
                f"Case: {case_id} | "
                f"Round: {run_index}/{runs}"
            )
            print(f"Input: {user_input}")
            print("=" * 80)

            agent = LLMAgent(max_steps=max_steps)
            result = run_eval_case(agent, case)

            result["case_id"] = case_id
            result["run_index"] = run_index
            result["tool_path"] = get_tool_path(result)

            results.append(result)

            status = (
                "PASS"
                if result.get("passed", "")
                else "FAIL"
            )

            classification = classify_failures(
                case=case,
                answer_passed=result.get("answer_passed", False),
                trace_passed=result.get("trace_passed", False),
                check_reasons=result.get("check_reasons", []),
            )
            result.update(classification)
            print(
                f"\n[{status}]"
                f"{case_id}"
                f"round {run_index}"
            )

            print("duration_seconds: "
                  f"{result.get('duration_seconds', 'N/A')}s")
            
            print("tool_path: "
                  f"{result.get('tool_path', 'N/A')}")
            
            # reasons = result.get("check_reasons", [])
            # if reasons:
            #     print("\nCheck Reasons:")
            #     for reason in reasons:
            #         print(f"- {reason}")

    return results

def build_case_summary(
    case: Dict[str, Any],
    case_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    生成单个评测用例的稳定性统计。
    """
    total_runs = len(case_results)

    passed_runs = sum(
        1
        for result in case_results
        if result.get("passed", False)
    )
    failed_runs = total_runs - passed_runs
    
    pass_rate = (
        passed_runs / total_runs
        if total_runs > 0
        else 0.0
    )

    durations = [
        result.get("duration_seconds", 0.0)
        for result in case_results
    ]

    average_duration = (
        mean(durations)
        if durations
        else 0.0
    )

    tool_path_counter = Counter()
    primary_failure_counter = Counter()

    for result in case_results:
        trace_summary = result.get("trace_summary", {})
        tool_calls = trace_summary.get("tool_calls", [])

        tool_path = " -> ".join(tool_calls)

        if not tool_path:
            tool_path = "(no tools)"
        
        tool_path_counter[tool_path] += 1

        primary_failure = result.get("primary_failure_type")

        if primary_failure:
            primary_failure_counter[primary_failure] += 1
    
    return {
        "case_id": case.get("id"),
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "pass_rate": pass_rate,
        "average_duration_seconds": round(average_duration, 3,),
        "p50_duration_seconds": round(percentile(durations, 0.50), 3),
        "p95_duration_seconds": round(percentile(durations, 0.95), 3),
        "tool_paths": dict(tool_path_counter),
        "primary_failure_types": dict(primary_failure_counter),
    }

def choose_primary_failure_type(failure_types):
    if not failure_types:
        return None
    
    for failure_type in FAILURE_PRIORITY:
        if failure_type in failure_types:
            return failure_type
        
    return "unknown"

def classify_failures(case, answer_passed, trace_passed, check_reasons):
    failure_types = []

    category = case.get("category", "general")
    
    for reason in check_reasons:
        if "禁止工具" in reason or "不允许的工具被调用" in reason:
            if category == "safety":
                failure_types.append("safety_violation")
            else:
                failure_types.append("tool_selection_error")

        if "缺少工具调用" in reason:
            failure_types.append("tool_selection_error")

        if "缺少必须关键词" in reason:
            failure_types.append("answer_mismatch")
        
        if "工具调用解析失败" in reason:
            failure_types.append("action_parse_error")
        
        if "期望成功的工具没有成功执行" in reason or "期望的工具调用失败" in reason:
            failure_types.append("tool_execution_error")
    
    overall_passed = (
        answer_passed and trace_passed
    )

    if not answer_passed:
        failure_types.append("answer_mismatch")
    if not trace_passed:
        failure_types.append("trajectory_mismatch")

    if not overall_passed and not failure_types:
        failure_types.append("unknown")

    failure_types = list(dict.fromkeys(failure_types))
    print(f"Failure types: {failure_types}")

    primary_failure_type = choose_primary_failure_type(
        failure_types
    )
    print(f"Primary failure type: {primary_failure_type}")
    return {
        "failure_types": failure_types,
        "primary_failure_type": primary_failure_type,
    }

def build_stability_summary(cases, results):
    case_summaries = []

    for case in cases:
        case_id = case.get("id", "unknown")

        case_results = [
            result
            for result in results
            if result.get("case_id") == case_id
        ]

        case_summary = build_case_summary(
            case, case_results
        )

        case_summaries.append(case_summary)

    total_runs = len(results)

    passed_runs = sum(
        1
        for result in results
        if result.get("passed", False)
    )

    overall_pass_rate = (
        passed_runs / total_runs
        if total_runs > 0
        else 0.0
    )

    return {
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "failed_runs": total_runs - passed_runs,
        "overall_pass_rate": overall_pass_rate,
        "cases": case_summaries,
        "tool_paths": [
            result.get("trace_summary", {}).get("tool_calls")
            for result in results
        ],
        "primary_failure_types": [
            result.get("primary_failure_type")
            for result in results
        ],
    }

def determine_exit_code(summary: dict) -> int:
    """
    根据稳定性测评摘要决定进程退出码

    所有运行结果均为通过时，退出码为0
    否则，退出码为1
    """
    return 0 if summary["failed_runs"] == 0 else 1


def main() -> int:
    cases = load_eval_cases()

    runs = 2
    max_steps = 5

    results = run_stability_eval(cases, runs, max_steps)

    summary = build_stability_summary(cases, results)

    print("\nAgent Stability Summary")
    print("=" * 80)
    print(f"总运行次数: {summary['total_runs']}")
    print(f"通过次数: {summary['passed_runs']}")
    print(f"失败次数: {summary['failed_runs']}")
    print(
        f"总体通过率: "
        f"{summary['overall_pass_rate'] * 100:.2f}%"
    )

    for case_summary in summary["cases"]:
        print("-" * 80)
        print(f"case_id: {case_summary['case_id']}")
        print(
            f"通过率: "
            f"{case_summary['pass_rate'] * 100:.2f}%"
        )
        print(
            "平均耗时: "
            f"{case_summary['average_duration_seconds']} 秒"
        )
        print(
            f"工具路径: "
            f"{case_summary['tool_paths']}"
        )
        print(
            "主要失败类型: "
            f"{case_summary['primary_failure_types']}"
        )

    return determine_exit_code(summary)

if __name__ == "__main__":
    sys.exit(main())
