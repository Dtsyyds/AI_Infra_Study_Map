import pytest
from group_anagrams import group_anagrams_counting, group_anagrams_sorting


def normalize(
    groups: list[list[str]],
) -> list[list[str]]:
    """消除组内顺序和分组顺序对测试的影响。"""
    return sorted(sorted(group) for group in groups)


@pytest.mark.parametrize(
    ("strs", "expected"),
    [
        (
            [
                "eat",
                "tea",
                "tan",
                "ate",
                "nat",
                "bat",
            ],
            [
                ["eat", "tea", "ate"],
                ["tan", "nat"],
                ["bat"],
            ],
        ),
        (
            [""],
            [[""]],
        ),
        (
            ["a"],
            [["a"]],
        ),
        (
            ["eat", "eat", "tea"],
            [["eat", "eat", "tea"]],
        ),
        (
            ["ab", "ba", "abc", "cab", "bca"],
            [
                ["ab", "ba"],
                ["abc", "cab", "bca"],
            ],
        ),
    ],
)
def test_group_anagrams(strs, expected):
    result1 = group_anagrams_sorting(strs)
    assert normalize(result1) == normalize(expected)
    result2 = group_anagrams_counting(strs)
    assert normalize(result2) == normalize(expected)
