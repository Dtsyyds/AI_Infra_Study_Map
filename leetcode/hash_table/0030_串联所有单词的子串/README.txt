# 0030. 串联所有单词的子串

## 题目链接：

https://leetcode.cn/problems/roman-to-integer/solutions/3990138/tan-xin-suan-fa-an-shun-xu-jian-diao-qia-ihsn/

## 题目类型：

- 哈希表  计数

## 题解

核心思想: 把“顺序匹配”变成“计数匹配”
假设 words = ["foo", "bar"] 那么合法的子串可以是 “foobar" 或 "barfoo"
我们不生成这两种排列俄俄，而是统计出：
"foo": 需要 1 次  "bar": 需要 1 次
然后，在 s 上截取一个长度为 2*3=6 的子串，检查它是否包含 1 个 "foo" 和 1 个 "bar" 如果个数刚好对应上，这个子串就是一个答案

哈希表计数
need 表记录每种单词的需求次数，window 表记录当前窗口内每种单词的实际次数

全排列暴力的另一个问题：每个可能的子串都要重新扫描一遍，做了很多重复工作
滑动窗口让窗口每次只向右移动一个单词，去掉左边一个单词，加入右边一个新单词，更新计数，极大提高了效率
由于所有单词长度相同，设单词长度 wordLen = 3, words 里面有 num = 3 个单词，窗口总长度固定为 totalLen = num * wordLen = 9



