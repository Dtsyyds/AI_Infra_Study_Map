"""
eval.py

Agent 自动化测评脚本

作用：
1. 读取 eval_cases.json
2. 逐条调用 LLMAgent
3. 判断最终回答是否包含 expected_contains 中的关键词
4. 输出通过率和失败想详情

这是 Agent Eval 的最小实现版本
"""

import os
import re
import json
import time
from typing import List, Dict, Any, Tuple, Optional

from agent import LLMAgent

EVAL_CASES_PATH = os.path.join(
    os.path.dirname(__file__),
    "eval_cases.json",
)

def load_eval_cases(path: str = EVAL_CASES_PATH) -> List[Dict[str, Any]]:
    """
    加载测评案例
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"测评文件不存在：{path}")
    
    with open(path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    for index, case in enumerate(cases):
        case_id = case.get("id", f"case_{index}")

        if not case.get("input"):
            raise ValueError(f"case {case_id} 没有配置 input")
        expected = case.get("expected_contains", [])

        if not isinstance(expected, list):
            raise ValueError(f"case {case_id} 的 expected_contains 不是列表")
        
        for keyword in expected:
            if not isinstance(keyword, str):
                raise ValueError(f"case {case_id} 的 expected_contains 中包含非字符串元素：{keyword}")

    return cases

def check_answer(answer: str, case: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    判断答案是否通过

    expected_contains 里面的所有关键词都必须出现在 answer 中
    """

    """
    检查 Agent 最终回答是否符合评测规则

    规则：
    1. expected_all 中的所有关键词都必须出现在 answer 中
    2. expected_any 中至少有一个关键词出现在 answer 中
    3. forbidden_contains 中的所有关键词都不能出现在 answer 中

    Returns:
        passed: 是否通过
        reasons: 未通过的原因列表
    """
    if answer is None:
        return False
    
    expected_all = case.get("expected_all", [])
    expected_any = case.get("expected_any", [])
    forbidden_contains = case.get("forbidden_contains", [])

    reasons = []

    missing_all = [
        keyword
        for keyword in expected_all
        if keyword not in answer
    ]

    if missing_all:
        reasons.append(
            f"缺少必须关键词: {missing_all}"
        )

    if expected_any:
        matched_any = {
            keyword
            for keyword in expected_any
            if keyword in answer
        }

        if not matched_any:
            reasons.append(
                f"以下关键词至少需要匹配一个: {expected_any}"
            )


    found_forbidden = [
        keyword
        for keyword in forbidden_contains
        if keyword in answer
    ]
    if found_forbidden:
        reasons.append(
            f"包含禁止关键词: {found_forbidden}"
        )

    passed = len(reasons) == 0

    return passed, reasons

def extract_tool_name(action: str) -> Optional[str]:
    """
    从 Action 中提取工具名

    示例：
    Action: calculator(expression="1+2")
    -> calculator

    Action: list_file(path="...")
    -> list_file

    Action: Finish[结果是 3]
    -> None
    """
    if not action:
        return None
    
    action_text = action.strip()
    action_text = action_text.replace("Action：", "Action:")

    if action_text.startswith("Action:"):
        action_text = action_text[len("Action:"):].strip()

    # Finish 不是工具调用需特殊处理
    if action_text.startswith("Finish"):
        return None
    
    pattern = r"(\w+)\s*\("
    match = re.search(pattern, action_text)
    if not match:
        return None
    
    return match.group(1)

def analyze_trace(
        trace_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    分析一次 Agent Trace
    
    Returns:
    - Trace 状态
    - 执行步骤数量
    - 调用过哪些工具
    - 哪些工具成功
    - 哪些工具失败
    """
    steps = get_trace_steps(trace_data)

    tool_calls: List[str] = []
    successful_tools: List[str] = []
    failed_tools: List[str] = []

    for step in steps:
        action = step.get("action", "")
        result = step.get("result", {})

        tool_name = extract_tool_name(action)

        # Finish 不算工具调用
        if tool_name is None:
            continue

        tool_calls.append(tool_name)

        if not isinstance(result, dict):
            failed_tools.append(tool_name)
            continue

        success = result.get("success")
        content = result.get("content")

        if success is True:
            successful_tools.append(tool_name)
        elif success is False:
            failed_tools.append(tool_name)

        elif (
            isinstance(success, str)
            and content.startswith("TOOL_OK:")
        ):
            successful_tools.append(tool_name)
        else:
            failed_tools.append(tool_name)

    return {
        "trace_id": trace_data.get("trace_id"),
        "trace_status": trace_data.get(
            "status",
            "unknown"
        ),
        "step_count": len(steps),
        "tool_calls": tool_calls,
        "successful_tools": successful_tools,
        "failed_tools": failed_tools,
    }

def check_trace(
    trace_data: Dict[str, Any],
    case: Dict[str, Any],
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    检查 Agent 执行轨迹是否符合预期

    支持规则：
    - expected_tools: 必须调用的工具
    - forbidden_tools: 禁止调用的工具
    - expected_status: 期望 Trace 状态
    - max_steps: 最大执行步骤
    - required_expected_tool_success: 期望工具是否必须执行成功
    """

    if not trace_data:
        return (
            False,
            ["没有获取到 Agent Trace"],
            {},
        )
    trace_summary = analyze_trace(trace_data)
    reasons: List[str] = []

    expected_tools = case.get("expected_tools", [])
    forbidden_tools = case.get("forbidden_tools", [])

    expected_status = case.get(
        "excepted_status",
        "success"
    )

    max_steps = case.get("max_steps")

    require_success = case.get(
        "require_expected_tool_success",
        True,
    )

    tool_calls = trace_summary["tool_calls"]
    successful_tools = trace_summary["successful_tools"]

    # 检查必须调用的工具
    missing_tools = [
        tool_name
        for tool_name in expected_tools
        if tool_name not in tool_calls
    ]

    if missing_tools:
        reasons.append(f"缺少工具调用：{missing_tools}")

    # 检查禁止调用的工具
    forbidden_tool_calls = [
        tool_name
        for tool_name in forbidden_tools
        if tool_name in tool_calls
    ]

    if forbidden_tool_calls:
        reasons.append(f"不允许的工具被调用：{forbidden_tool_calls}")

    # 检查期望工具是否成功
    if require_success:
        unsuccessful_tools = [
            tool_name
            for tool_name in expected_tools
            if tool_name not in successful_tools
        ]

        if unsuccessful_tools:
            reasons.append(f"期望的工具调用失败：{unsuccessful_tools}")

    # 检查 Trace 状态
    actual_status = trace_summary["trace_status"]

    if(
        expected_status
        and actual_status != expected_status
    ):
        reasons.append(f"期望的 Trace 状态是 {expected_status}，实际为 {actual_status}")

    # 检查执行步数
    step_count = trace_summary["step_count"]

    if(
        isinstance(max_steps, int)
        and step_count > max_steps
    ):
        reasons.append(f"执行步数超过 {max_steps}")

    return (
        not reasons,
        reasons,
        trace_summary,
    )

def run_eval_case(agent: LLMAgent, case: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行单个测评案例
    """
    """
    同时得到：
    1. 最终答案
    2. Agent 执行轨迹
    """
    case_id = case.get("id", "unknown")
    user_input = case.get("input", "")
    expected_all = case.get("expected_all", [])
    expected_any = case.get("expected_any", [])
    forbidden_contains = case.get("forbidden_contains", [])

    start_time = time.time()

    try:
        answer = agent.run(user_input)
        error = None
    except Exception as e:
        answer = ""
        error = str(e)

    duration_seconds = round(time.time() - start_time, 3)

    trace_data: Dict[str, Any] = {}

    if agent.last_trace is not None:
        trace_data = agent.last_trace.snapshot()

    
    passed = False
    check_reasons = []

    if error is None:
        # passed = check_answer(answer, expected_contains=expected_contains)
        answer_passed, answer_reasons = check_answer(answer, case)
        trace_passed, trace_reasons, trace_summary = check_trace(trace_data, case) 

        # 最终答案和执行轨迹必须同时通过
        passed = answer_passed and trace_passed

        check_reasons.extend(answer_reasons + trace_reasons)

    else:
        passed = False
        
        check_reasons = [
            f"执行出错: {error}",
        ]
        trace_summary = {}

    return {
        "id": case_id,
        "input": user_input,\
        "description": case.get("description", ""),

        "expected_all": expected_all,
        "expected_any": expected_any,
        "forbidden_contains": forbidden_contains,

        "expected_tools": case.get("expected_tools", []),
        "forbidden_tools": case.get("forbidden_tools", []),
        "excepted_status": case.get("expected_status", "success"),
        "max_steps": case.get("max_steps"),

        "answer": answer,
        "passed": passed,
        "check_reasons": check_reasons,
        "trace_summary": trace_summary,

        "error": error,
        "duration_seconds": duration_seconds,
    }

def print_eval_result(results: List[Dict[str, Any]]) -> None:
    """
    打印测评结果
    """
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = total - passed_count

    pass_rate = 0.0

    if total > 0:
        pass_rate = passed_count / total * 100

    print("\nAgent Eval Result")
    print("=" * 80)
    print(f"总用例数: {total}")
    print(f"通过数: {passed_count}")
    print(f"失败数: {failed_count}")
    print(f"通过率: {pass_rate:.2f}%")
    print("=" * 80)

    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        # print(result)
        print(f"[{status}] {result.get('id', '')}")
        print(f"input: {result.get('input', '')}")
        print(f"expected_all: {result.get('expected_all', [])}")
        print(f"expected_any: {result.get('expected_any',[])}")
        print(f"forbidden_contains: {result.get('forbidden_contains', [])}")
        print(f"answer: {result.get('answer', '')}")
        print(f"duration_seconds: {result.get('duration_seconds',0)}")

        if not result["passed"]:
            print(f"check_reasons: {result.get('check_reasons', [])}")

        if result["error"]:
            print(f"error: {result.get('error', '')}")

        trace_summary = result.get(
            "trace_summary",
            {},
        )

        print(
            f"expected_tools: "
            f"{result.get('expected_tools', [])}"
        )

        print(
            f"forbidden_tools: "
            f"{result.get('forbidden_tools', [])}"
        )

        print(
            f"trace_status: "
            f"{trace_summary.get('trace_status', 'unknown')}"
        )

        print(
            f"step_count: "
            f"{trace_summary.get('step_count', 0)}"
        )

        print(
            f"tool_calls: "
            f"{trace_summary.get('tool_calls', [])}"
        )

        print(
            f"successful_tools: "
            f"{trace_summary.get('successful_tools', [])}"
        )

        print(
            f"failed_tools: "
            f"{trace_summary.get('failed_tools', [])}"
        )

        print("-" * 80)

# 数据生成端保证结构正确
# 数据消费去端做好缺省保护
def save_eval_results(results: List[Dict[str, Any]]) -> None:
    """
    保存评测结果到本地 JSON 文件。
    """
    output_dir = os.path.join(
        os.path.dirname(__file__),
        "eval_results"
    )

    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, "latest_eval_result.json")

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])

    summary = {
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": passed_count / total * 100 if total > 0 else 0.0,
        "results": results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n评测结果已保存: {output_path}")

def validate_eval_cases(cases: List[Dict[str, Any]],) -> None:
    """
    检查测评集格式是否合法
    """

    case_ids = set()

    for index, case in enumerate(cases):
        case_id = case.get("id")

        if not case_id:
            raise ValueError(
                f"第 {index + 1} 个用例缺少 id"
            )
        
        if case_id in case_ids:
            raise ValueError(
                f"第 {index + 1} 个用例的 id '{case_id}' 与其他用例重复"
            )
        
        case_ids.add(case_id)

        if not case.get("input"):
            raise ValueError(
                f"第 {index + 1} 个用例缺少 input"
            )
        
        expected_all = case.get("expected_all", [])
        expected_any = case.get("expected_any", [])

        if not expected_all and not expected_any:
            raise ValueError(
                f"第 {index + 1} 个用例缺少 expected_all 和 expected_any"
            )
        
        for field_name in [
            "expected_all",
            "expected_any",
            "forbidden_contains",
        ]:
            value = case.get(field_name, [])

            if not isinstance(value, list):
                    raise ValueError(
                        f"第 {index + 1} 个用例的 '{field_name}' 应该是一个列表，但实际类型是 {type(value)}"
                    )
            
            if not all(isinstance(item, str) for item in value):
                    raise ValueError(
                        f"第 {index + 1} 个用例的 '{field_name}' 应该是一个字符串列表，但实际类型是 {type(value)}"
                    )

def get_trace_steps(
    trace_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    获取 Trace 中的执行步骤
    """
    steps = trace_data.get("steps")

    if not isinstance(steps, list):
        return []
    
    return steps

def main() -> None:
    cases = load_eval_cases()
    validate_eval_cases(cases)

    results = []

    for case in cases:
        print("=" * 80)
        print(f"Running eval case: {case.get('id')}")
        print(f"Input: {case.get('input')}")
        print("=" * 80)
        agent = LLMAgent(max_steps=5)
        result = run_eval_case(agent, case)
        results.append(result)

    print_eval_result(results)
    save_eval_results(results)



if __name__ == "__main__":
    main()


