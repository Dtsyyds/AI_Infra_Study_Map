import pytest

from contains_duplicate import (
    contains_duplicate,
    contains_duplicate_bruteforce,
)


@pytest.mark.parametrize(
    ("nums", "expected"),
    [
        ([1, 2, 3, 1], True),
        ([1, 2, 3, 4], False),
        ([1, 1], True),
        ([], False),
        ([7], False),
        ([-1, 0, -1], True),
    ],
)
def test_contains_duplicate(nums, expected):
    assert contains_duplicate_bruteforce(nums) is expected
    assert contains_duplicate(nums) is expected
