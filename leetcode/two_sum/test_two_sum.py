import pytest

from two_sum import two_sum, two_sum_bruteforce


@pytest.mark.parametrize(
    ("nums", "target", "expected"),
    [
        ([2, 7, 11, 15], 9, [0, 1]),
        ([3, 2, 4], 6, [1, 2]),
        ([3, 3], 6, [0, 1]),
        ([0, 4, 3, 0], 0, [0, 3]),
        ([-1, -2, -3, -4, -5], -8, [2, 4]),
    ],
)
def test_two_sum(nums, target, expected):
    assert two_sum_bruteforce(nums, target) == expected
    assert two_sum(nums, target) == expected


@pytest.mark.parametrize(
    "function",
    [
        two_sum_bruteforce,
        two_sum,
    ],
)
def test_two_sum_raises_when_no_solution(function):
    with pytest.raises(
        ValueError,
        match="不存在满足条件的两个元素",
    ):
        function([1, 2, 3], 100)
