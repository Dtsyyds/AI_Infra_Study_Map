# 1.  基础输入字符串
name = input("请输入您的名字：")

# 2. 输入数字
age = int(input("请输入您的年龄："))

# 3. 输入小数
score = float(input("请输入您的成绩："))

# 4. 一行输入多个值
data = input("请输入您的姓名、性别和身高，用空格隔开：").split()
if len(data) == 3:
    name, gender, height = data[0], data[1], float(data[2])

# 5. 安全输入莫玛，隐藏回显
from getpass import getpass

password = getpass("请输入您的密码：")