from typing import List

class Solution:
    def maxArea(self, height: List[int]) -> int:
        left = 0
        right = len(height) - 1

        max_Area = 0
        cur_Area = 0
        while(left < right):
            if height[left] < height[right]:
                cur_Area = (right - left) * height[left]
                left += 1
            else:
                cur_Area = (right - left) * height[right]
                right -= 1
            
            max_Area = max(cur_Area, max_Area)
        
        return max_Area

