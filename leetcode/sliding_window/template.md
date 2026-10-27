"""
滑动窗口的两种题型:最长和最短
"""

# 求 最长子串/子数组

``` 
left = 0
for right in range(1000):
    # 把 s[right] 加入窗口
    while 窗口不满足条件:
        # 把 s[left] 从窗口移除
        left += 1

    ans = max(ans, right - left + 1)    # 此时窗口满足条件
``` 

# 求 最短子串/子数组

``` 
left = 0
for right in range(n):
    # 把 s[right] 加入窗口
    while 窗口满足条件:
        ans = min(ans, right - left + 1)
        left += 1
``` 

连续子数组/子串 + 最长/最短 + 满足条件 用滑动窗口解决

```
from collections import Counter
window = Counter()
window[c] += 1
window[c] -= 1
if window[c] == 0:del window[c]     # 删除计数为0的元素
```   