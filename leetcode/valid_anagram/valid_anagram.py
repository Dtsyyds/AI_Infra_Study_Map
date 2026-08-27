def is_anagram_sorting(s: str, t: str) -> bool:
    """使用排序判断两个字符串是否为字母异位词。"""
    return sorted(s) == sorted(t)


def is_anagram(s: str, t: str) -> bool:
    """使用字符计数表判断两个字符串是否为字母异位词。"""
    if len(s) != len(t):
        return False

    counter = [0] * 26
    for i in range(len(s)):
        counter[ord(s[i]) - ord("a")] += 1
        counter[ord(t[i]) - ord("a")] -= 1

    for count in counter:
        if count != 0:
            return False

    return True


def is_anagram_hash(s: str, t: str) -> bool:
    """使用哈希表判断两个字符串是否为字母异位词，使用字典计数，支持任意可哈希字符"""
    if len(s) != len(t):
        return False

    counter = {}

    for char in s:
        counter[char] = counter.get(char, 0) + 1

    for char in t:
        if char not in counter:
            return False
        counter[char] = counter.get(char, 0) - 1

    for count in counter.values():
        if count != 0:
            return False

    return True

"""
为什么固定 26 位数组的空间复杂度是 O(1)，字典版本通常是 O(n)？
如果题目明确只有小写英文字母，你更倾向数组还是字典？为什么？
len(s) != len(t) 为什么应该提前判断？

1. 固定 26 位就是确定了字典的大小，所以是 O(1)。字典版本的大小取决于字符集的大小，通常是 O(n)（n 是字符集中不同元素的数量）。
2. 用数组，可以节省空间
3. 因为如果长度不同，那么它们一定不是字母异位词。这样可以提前返回结果，避免不必要的计算。
"""
