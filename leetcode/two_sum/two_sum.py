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


""""
Two Sum 为什么使用字典而不是集合？
为什么必须先查找 complement，再保存当前元素？
[3, 2, 4]、target=6 时，哈希表每一步如何变化？
哈希解法用空间换取了什么？

1. 字典能存储键值对，集合只能存储元素
2. 保证哈希表里只有当前元素之前的元素，防止当前元素与自己配对。
3. {} -> {3:0} -> {3:0, 2:1} 
4. 使用最多 O(n) 的额外空间保存历史元素，把补数查找从遍历数组的 O(n) 降到平均 O(1)，从而把总时间从 O(n²) 优化为平均 O(n)
"""
