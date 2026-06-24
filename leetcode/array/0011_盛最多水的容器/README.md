# 0004. 寻找两个正序数组的中位数

## 题目链接：

https://leetcode.cn/problems/container-with-most-water/solutions/3987424/sheng-zui-duo-shui-de-rong-qi-san-ge-si-2pe6t/

## 题目方法：

- 直接求出所有可能的解，找出最大值
- 双指针思路

## 题解

- 暴力解法

通过两个for循环迭代，求出所有可能的解，然后找出最大值，会超时

- 双指针思路

自己想的从中间向两边扩展，原因就是保证底盘再再一直在变大，但是这种方法会漏掉一些解
deepseek提供的思路是从两边向中间拓展，避免漏解

## 官方题解 - 二分查找
