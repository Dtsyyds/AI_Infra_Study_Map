from typing import List


def two_sum_bruteforce(
    nums: List[int],
    target: int,
) -> List[int]:
    """使用两层循环寻找两个元素的下标。"""
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return [i, j]
    raise ValueError("不存在满足条件的两个元素")


def two_sum(
    nums: List[int],
    target: int,
) -> List[int]:
    """使用哈希表寻找两个元素的下标。"""
    hash_table = {}
    for index, num in enumerate(nums):
        complement = target - num
        if complement in hash_table:
            return [hash_table[complement], index]
        hash_table[num] = index
    raise ValueError("不存在满足条件的两个元素")
