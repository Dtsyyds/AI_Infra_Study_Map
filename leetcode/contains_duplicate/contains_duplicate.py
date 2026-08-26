from typing import List

def contains_duplicate_bruteforce(nums: List[int]) -> bool:
    """使用两层循环判断是否存在重复元素。"""
    # TODO：你来实现
    if len(nums) < 2:
        return False

    # print(range(len(nums))
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] == nums[j]:
                return True
    return False


def contains_duplicate(nums: List[int]) -> bool:
    """使用集合判断是否存在重复元素。"""
    # TODO：你来实现
    seen = set()
    for num in nums:
        if num in seen:
            return True
        seen.add(num)
    return False