## 基本语法

``` python
st = []     # 用列表 list 模拟栈
st.append(0)    
st[-1]      # 取栈顶
st.pop()    # 弹出并返回栈顶元素
```

``` python
from collections import deque
q = deque()
q.append(0)
q[0]
q.popleft()
```

### 模板

``` python
stack = []      # 栈的初始化
for char in s:
    if char == "(":
        stack.append(char)
    else:
        if not stack:   # 栈判断空
            return False
        top = stack.pop()   # 弹出并取值

        if top != "(":
            return False

return not stack

from collections import deque

q = deque()
q.append(x)             # 入队（尾部）
left = q.popleft()      # 出队（头部）
first = q[0]            # 查看队首（不移除）
```


