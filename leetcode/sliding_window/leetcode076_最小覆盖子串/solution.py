from collections import Counter

class Solution:
    def minWindow(self, s: str, t: str) -> str:
        need = Counter(t)
        window = Counter()
        left = 0
        valid = 0
        start, min_len = 0, float('inf')

        for right, ch in enumerate(s):
            if ch in need:
                window[ch] += 1
                if window[ch] == need[ch]:
                    valid += 1

            while valid == len(need):
                if right - left + 1 < min_len:
                    start = left
                    min_len = right - left + 1

                out = s[left]
                if out in need:
                    window[out] -= 1
                    if window[out] < need[out]:
                        valid -= 1
                left += 1
                    

        return "" if min_len == float('inf') else s[start: start + min_len]

if __name__ == "__main__":
    s = "ADOBECODEBANC"
    t = "ABC"
    print(Solution().minWindow(s, t))

def can_retry_attempt(
    *,
    retryable: bool,
    attempt_no: int,
    max_attempts: int,
) -> bool:
    """max_attempts 包含首次执行。"""
    if attempt_no < 1 or max_attempts < 1:
        raise ValueError("attempt_no 和 max_attempts 必须至少为 1")

    # TODO：
    # 1. 失败不允许重试时，返回 False。
    if retryable is False:
        return False
    # 2. 执行次数已达到或超过上限时，返回 False。
    elif attempt_no >= max_attempts:
        return False
    else:
        return True
    # 3. 其余情况返回 True。
    raise NotImplementedError("完成重试资格判断")
            