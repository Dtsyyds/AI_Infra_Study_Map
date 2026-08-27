import pytest

from valid_anagram import is_anagram, is_anagram_sorting, is_anagram_hash


@pytest.mark.parametrize(
    ("s", "t", "expected"),
    [
        ("anagram", "nagaram", True),
        ("rat", "car", False),
        ("aab", "abb", False),
        ("", "", True),
        ("a", "a", True),
        ("ab", "a", False),
        ("aacc", "ccac", False),
    ],
)
def test_valid_anagram(s, t, expected):
    assert is_anagram_sorting(s, t) is expected
    assert is_anagram(s, t) is expected
    assert is_anagram_hash(s, t) is expected
