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


"""
为什么内层循环必须从 i + 1 开始？
当数组长度为 5 且没有重复元素时，暴力解法比较几次？
为什么暴力解法的额外空间是 O(1)？
为什么集合解法的额外空间是 O(n)？
输入 [1, 2, 3, 1] 时，集合 seen 每一步分别是什么？

1. 内层从 i + 1 开始为了避免比较自己也避免重复比较之前已经比较的元素
2. 4 + 3 + 2 + 1 = 10 次
3. 因为只使用了几个变量，所以额外空间是 O(1)
4. 因为使用了集合存储元素，所以额外空间是 O(n)
5. 1, 2, 3 

"""
