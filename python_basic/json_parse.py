# 核心基础：解析 parse 将 JSON 转成 python 对象
# 将文本或文件转成 Python 的字典或列表

"""
类型映射表
JSON object -> Python object
JSON array -> Python list
JSON string -> Python str
JSON number -> Python int or float
JSON null -> Python None

json.loads()          # 解析 JSON 字符串 eg: data = json.loads('{"name": "Tom"}'))
json.load()           # 解析 JSON 文件 eg: data = json.load(open('data.json'))
"""

import json
from pprint import pprint

api_data = {
    "code": 200,
    "data": {
        "users": [
            {"id": 1, "name": "张三", "profile": {"age": 25, "city": "北京"}},
            {"id": 2, "name": "李四", "profile": {"age": 30, "city": None}}
        ]
    },
    "total": 2
}

# 基本提取 键名索引 + 下标
name = api_data["data"]["users"][0]["name"]
print(name)

# 安全提取 直接索引 [] 在键不存在时会报错崩溃（KeyError）
city = api_data.get("data", {}).get("users", [{}])[0].get("profile", {}).get("city" "未知城市")
print(city)

# 提取列表并遍历
users =  api_data["data"]["users"]
for user in users:
    print(user["name"])



