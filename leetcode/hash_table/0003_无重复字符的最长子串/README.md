# 0003. 无重复字符的最长子串

## 题目链接：

https://leetcode.cn/problems/longest-substring-without-repeating-characters/solutions/3989998/wu-zhong-fu-zi-fu-de-zui-chang-zi-chuan-vjhid/

## 题目类型：

- 哈希表
- 哈希表 + 滑动窗口 + 双指针

## 题解

- 哈希表

依次遍历字符串中的字符，用一个哈希表记录字符出现的索引，用一个变量记录当前字符串的左边界，一旦出现重复的字符，更新左边界到当前的下一个字符，依次迭代更新找到最长子串的长度。
