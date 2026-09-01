from collections import defaultdict


def group_anagrams_sorting(words: list[str]) -> list[list[str]]:
    """
    将 words 中的 word 排序后，相同的 word 放在同一个 list
    """
    groups = defaultdict(list)

    for word in words:
        sorted_word = "".join(sorted(word))  # 排序后作为键
        groups[sorted_word].append(word)

    return list(groups.values())


def group_anagrams_counting(words: list[str]) -> list[list[str]]:
    groups = defaultdict(list)

    for word in words:
        count = [0] * 26
        for ch in word:
            count[ord(ch) - ord("a")] += 1
        groups[tuple(count)].append(word)

    return list(groups.values())
