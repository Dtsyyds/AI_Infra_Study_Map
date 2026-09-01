class Solution:
    def isValid(self, s: str) -> bool:
        stack = []
        for char in s:
            if char == "(" or char == "{" or char == "[":
                stack.append(char)
            else:
                if not stack:
                    return False
                top = stack.pop()
                if char == ")":
                    if top != "(":
                        return False
                if char == "}":
                    if top != "{":
                        return False
                if char == "]":
                    if top != "[":
                        return False
        return not stack

class Solution_good:
    def isValid(self, s: str) -> bool:
        # 建立哈希映射,左括号做键,右括号做值
        pairs = {")": "(", "}": "{", "]": "["}
        stack = []
        for char in s:
            # 如果当前字符是右括号（即存在于字典的键中）
            if char in pairs:
                # 弹出栈顶元素，如果栈为空则给一个虚拟值
                top_element = stack.pop() if stack else '#'
                # 检查弹出的栈顶元素是否与当前的右括号匹配
                if pairs[char] != top_element:
                    return False
            else:
                # 如果是左括号，则入栈
                stack.append(char)
        # 如果栈为空，则所有的括号都是匹配的
        return not stack


        