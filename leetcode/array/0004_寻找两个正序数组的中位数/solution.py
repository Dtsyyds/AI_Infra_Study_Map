from typing import List

class Solution:
    def findMedianSortedArrays(self, nums1: List[int], nums2: List[int]) -> float:
        if len(nums1) > len(nums2):
            return self.findMedianSortedArrays(nums2, nums1)
        
        m = len(nums1)
        n = len(nums2)

        left = 0
        right = m
        leftsize = (m + n + 1) // 2

        while left <= right:
            i = (left + right) // 2
            j = leftsize - i

            Aleft = nums1[i-1] if i > 0 else float('-inf')
            Bleft = nums2[j-1] if j > 0 else float('-inf')
            Aright = nums1[i] if i < m else float('inf')
            Bright = nums2[j] if j < n else float('inf')

            if Aleft <= Bright and Bleft <= Aright:
                if (m + n) % 2 == 0:
                    return (max(Aleft, Bleft) + min(Aright, Bright)) / 2
                else:
                    return max(Aleft, Bleft)
            elif Aleft > Bright:
                right = i - 1