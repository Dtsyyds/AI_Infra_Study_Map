class Solution:
    def minEatingSpeed(self, piles: list[int], h: int) -> int:
        def check(x):
            return sum((pile-1) // x + 1 for pile in piles) <= h

        l, r = 1, max(piles)
        while l < r:
            m = (l + r) // 2
            if check(m):
                r = m
            else:
                l = m + 1
        return l

if __name__ == '__main__':
    s = Solution()
    print(s.minEatingSpeed([3, 6, 7, 11], 8))  # Output: 4
    print(s.minEatingSpeed([30, 11, 23, 4, 20], 5))  # Output: 30
    print(s.minEatingSpeed([30, 11, 23, 4, 20], 6))  # Output: