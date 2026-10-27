from typing import List

class Solution:
    def searchRange(self, nums: List[int], target: int) -> List[int]:
        left, right = 0, len(nums) - 1
        left_index, right_index = -1, -1
        while left <= right:
            mid = (left + right) // 2
            if nums[mid] == target:
                left_index = mid
                right_index = mid
                while left_index > 0 and nums[left_index - 1] == target:
                    left_index -= 1
                while right_index < len(nums) - 1 and nums[right_index + 1] == target:
                    right_index += 1
                return [left_index, right_index]
            elif nums[mid] < target:
                left = mid + 1
            else:
                right = mid - 1

        return [-1, -1]

class Solution_Pro:
    def searchRange(self, nums: list[int], target: int) -> list[int]:
        def find_bound(is_left: bool) -> int:
            left, right = 0, len(nums) - 1
            bound = -1
            while left <= right:
                mid = (left + right) // 2
                if nums[mid] < target:
                    left = mid + 1
                elif nums[mid] > target:
                    right = mid - 1
                else:
                    bound = mid
                    if is_left:
                        right = mid - 1
                    else:
                        left = mid + 1
            return bound

        left_bound = find_bound(True)
        right_bound = find_bound(False)
        return [left_bound, right_bound]