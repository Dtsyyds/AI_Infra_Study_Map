# 核心函数速览
"""
re.match(pattern, string)       # 从字符串的开头开始匹配正则表达式 返回 match 对象或 None
re.search(pattern, string)      # 扫描整个字符串并返回第一个成功的匹配 返回 match 对象或 None
re.findall(pattern, string)     # 返回所有匹配的字符串列表 返回列表
re.sub(pattern, repl, string)   # 将字符串中所有匹配的字符串替换为指定的字符串 返回替换后的字符串
re.finditer(pattern, string)    # 返回一个迭代器，迭代器的每个元素都是一个 match 对象
re.split(pattern, string)       # 将字符串按照正则表达式分割成一个列表 返回列表
"""

import re

# text = "我的电话是 138-0965-4278"
# pattern = r"(\d{3})-(\d{4})-(\d{4})"
# m = re.search(pattern, text)

# if m:
#     print(m.group())        # 138-0965-4278
#     print(m.group(1))       # 138
#     print(m.group(2))       # 0965

#     print(m.groups())       # ('138', '0965', '4278')
#     print(m.start())        # 6
#     print(m.end())          # 19 end是匹配结束的下一个索引（即最后一个匹配字符的索引+1）
#     print(m.span())         # (6, 19) [6, 19)

# # 字符类与量词
# text1 = "张三和李四、王五赵六去了北京。"
# # names = re.findall(r"[张李王赵][三四五六]", text1)
# # print(names)            # ['张三', '李四', '王五', '赵六']
# # 连续多个中文字符
# names = re.findall(r"[\u4e00-\u9fa5]{2,4}", text1)   # {2,4}之间不能有空格，代表重复2到4次
# print(names)            # ['张三和李', '王五赵六', '去了北京']

# 贪婪与非贪婪
# 默认量词（*, +, ?, {m,n}）是贪婪的，会尽量多匹配。在后面加 ? 变为非贪婪。
html = "<div>标题</div><div>内容</div>"
# 贪婪：匹配到最后一个</div>
print(re.findall(r"<div>.*</div>", html))       # ['<div>标题</div><div>内容</div>']

# 非贪婪：匹配到第一个</div>
print(re.findall(r"<div>.*?</div>", html))      # ['<div>标题</div>', '<div>内容</div>']

# 分组与命名分组
text = "2026-06-25 15:54:00"
pattern = r"(?P<year>\d{4})-"

