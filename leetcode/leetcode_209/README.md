暴力枚举所有子数组的复杂度是多少？
因为数组元素全是正数，当窗口和已经大于等于 target 时，应该移动哪一个指针？

1. n + n-1 + n-2 + n-3 + ... + 1 = n(n+1) // 2,i时间复杂度 O(n(n+1)//2)
2. 移动左指针

``` python
def minSubArrayLen(target: int, nums: list) -> int:
    left, sum = 0
    answer = len(nums) - 1

    while left < answer:
        for i in range(left, answer):
               




```