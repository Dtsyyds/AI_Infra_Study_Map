from collections import Counter

class Solution:
    def lengthOfLongestSubstring(self, s: str) -> int:
        left = 0
        right = 0
        window = Counter()
        max_len = 0
        while right < len(s):
            window[s[right]] += 1
            while window[s[right]] > 1:
                window[s[left]] -= 1
                left += 1
            max_len = max(max_len, right - left + 1)
            right += 1
        return max_len

if __name__ == '__main__':
    s = "bb"
    print(Solution().lengthOfLongestSubstring(s))  # Output: 3