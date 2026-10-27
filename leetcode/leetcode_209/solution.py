from typing import List

class Solution:
    def minSubArrayLen(self, target: int, nums: List[int]) -> int:
        left = 0
        sum_nums = 0
        answer = len(nums) + 1
        for right in range(len(nums)):
            sum_nums += nums[right]
            while sum_nums >= target:
                sum_nums -= nums[left]
                left += 1
                answer = min(answer, right - left + 2)
        return answer if answer != len(nums) + 1 else 0
        
