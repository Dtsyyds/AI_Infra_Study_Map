class Solution:
    def minSubArrayLen(self, target: int, nums: list[int]) -> int:
        left = 0
        min_len = len(nums)

        for right in range(len(nums)):
            sum = 0
            for i in range(left, right + 1):
                sum += nums[i]
                while sum >= target:
                    min_len = min(min_len, right - left + 1)
                    sum -= nums[left]
                    left += 1
        return min_len

    def solution(self, target: int, nums: list[int]) -> int:
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

if __name__ == "__main__":
    print(Solution().minSubArrayLen(4, [1, 4, 4]))
                