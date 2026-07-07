# 0141. 环形链表

## 题目链接：

https://leetcode.cn/problems/linked-list-cycle/solutions/3992629/shuang-zhi-zhen-by-dtshuai-vw6i/

## 题目类型：

- 双指针

## 题解

- 双指针

对于有序数组，使用相向指针的思路，一个指向左边界，另一个指向右边界，每次判断两数之和，根据和的大小移动指针，直到相遇。