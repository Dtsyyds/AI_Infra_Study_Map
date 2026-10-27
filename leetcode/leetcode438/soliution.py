from typing import List

class Solution:
    def findAnagrams(self, s: str, p: str) -> List[int]:
        p_count = [0] * 26
        s_count = [0] * 26

        left = 0
        result = []

        for i in range(len(p)):
            p_count[ord(p[i]) - ord('a')] += 1

        while left + len(p) <= len(s):
            for i in range(left, left + len(p)):
                s_count[ord(s[i]) - ord('a')] += 1
            if p_count == s_count:
                result.append(left)
            s_count = [0] * 26
            left += 1

        return result

    def findAnagramsGood(self, s: str, p: str) -> List[int]:
        p_count = [0] * 26
        s_count = [0] * 26
        left = 0
        result = []

        for i in range(len(p)):
            p_count[ord(p[i]) - ord('a')] += 1

        for right in range(len(s)):
            s_count[ord(s[right]) - ord('a')] += 1
            if right - left + 1 > len(p):
                s_count[ord(s[left]) - ord('a')] -= 1
                left += 1
            if right - left + 1 == len(p) and s_count == p_count:
                result.append(left)

        return result
        
