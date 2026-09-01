import pytest
from majority_element import (
    majority_element_hash,
    majority_element_boyer_moore,
)


@pytest.mark.parametrize(
    ("nums", "expected"),
    [
        ([3, 2, 3], 3),
        ([2, 2, 1, 1, 1, 2, 2], 2),
        ([6, 5, 5], 5),
        ([1], 1),
        (
            [
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
            ],
            -1,
        ),
    ],
)
def test_majority_element(nums, expected):
    assert majority_element_hash(nums) == expected
    assert majority_element_boyer_moore(nums) == expected
