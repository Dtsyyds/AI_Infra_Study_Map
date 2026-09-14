from eval_stability import classify_failures


def check_classifier(
        test_name,
        case, 
        answer_passed,
        trace_passed,
        check_reasons,
        expected_types, 
        expected_primary
):
    result = classify_failures(case, answer_passed, trace_passed, check_reasons)

    actual_types = result.get("failure_types", [])
    actual_primary = result.get("primary_failure_type")

    assert actual_types == expected_types, f"{test_name} - failure types mismatch. Expected: {expected_types}, Actual: {actual_types}"

    assert actual_primary == expected_primary, f"{test_name} - primary failure type mismatch. Expected: {expected_primary}, Actual: {actual_primary}"

    print(f"[PASS] {test_name}")

def test_safety_violation():
    check_classifier(
        test_name="safety_violation",

        case={
            "category": "safety"
        },

        answer_passed=True,
        trace_passed=False,

        check_reasons=[
            "调用了禁止工具: ['read_file']"
        ],

        expected_types=[
            "safety_violation",
            "trajectory_mismatch",
        ],

        expected_primary="safety_violation",
    )

def test_basic_tool_fail():
    check_classifier(
        test_name="tool_use",

        case={
            "category": "tool_use"
        },

        answer_passed=False,
        trace_passed=False,

        check_reasons=[
            "调用了禁止工具: ['read_file']",
            "缺少必须关键词：['9]",
        ],

        expected_types=[
            "tool_selection_error",
            "answer_mismatch",
            "trajectory_mismatch"
        ],

        expected_primary="tool_selection_error",
    )

def test_expected_tool_fail():
    check_classifier(
        test_name="tool_fail",

        case={
            "category": "tool_use"
        },

        answer_passed=False,
        trace_passed=False,

        check_reasons=[
            "期望成功的工具没有成功执行: ['calculator']"
        ],

        expected_types=[
            "tool_execution_error",
            "answer_mismatch",
            "trajectory_mismatch",
        ],

        expected_primary="tool_execution_error",
    )

def test_unknown_fail():
    check_classifier(
        test_name="unknown",

        case={
            "category": "file_operation"
        },

        answer_passed=True,
        trace_passed=True,

        check_reasons=[
            
        ],

        expected_types=[
        ],

        expected_primary=None,
    )

def test_stopped_trace_is_classified_as_repeated_action():
    result = classify_failures(
        case={
            "category": "tool_use",
        },
        answer_passed=False,
        trace_passed=False,
        check_reasons=[
            "期望的 Trace 状态是 success，实际为 stopped",
        ],
        trace_status="stopped",
    )

    assert result["failure_types"] == [
        "repeated_action",
        "answer_mismatch",
        "trajectory_mismatch",
    ]

    assert (
        result["primary_failure_type"]
        == "repeated_action"
    )

def test_llm_error_trace_is_classified_as_api_error():
    result = classify_failures(
        case={
            "category": "llm",
        },
        answer_passed=False,
        trace_passed=False,
        check_reasons=[
            "期望的 Trace 状态是 success，实际为 llm_error",
        ],
        trace_status="llm_error",
    )

    assert result["failure_types"] == [
        "api_error",
        "answer_mismatch",
        "trajectory_mismatch",
    ]

    assert (
        result["primary_failure_type"]
        == "api_error"
    )

def test_llm_timeout_trace_is_classified_as_api_timeout():
    result = classify_failures(
        case={
            "category": "llm",
        },
        answer_passed=False,
        trace_passed=False,
        check_reasons=[
            "期望的 Trace 状态是 success，实际为 llm_timeout",
        ],
        trace_status="llm_timeout",
    )

    assert result["failure_types"] == [
        "api_timeout",
        "answer_mismatch",
        "trajectory_mismatch",
    ]

    assert result["primary_failure_type"] == "api_timeout"
    
if __name__ == "__main__":
    test_safety_violation()
    test_basic_tool_fail()
    test_expected_tool_fail()
    test_unknown_fail()