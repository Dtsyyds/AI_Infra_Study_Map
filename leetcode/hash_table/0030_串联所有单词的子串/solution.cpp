# include <iostream>
# include <string>
# include <vector>
# include <unordered_map>
# include <algorithm>
# include <unordered_set>

using namespace std;

class Solution {
    public:
        vector<int> findSubstring(string s, vector<string>& words)
        {
            // 自己不成熟的思路 先把words 里面所有的组合全部搭配出来 然后按长度去 s 中出现的字符串匹配
            int num = words.size();
            int length = words[0].size();

            int Length = num * length;

            vector<int> result;

            if(s.size() < Length || num == 0)
            {
                return result;
            }

            // unordered_map<string, int> map;
            // sort(words.begin(), words.end());   // 必须排序，保证从最小的字典序开始

            // // 在 C++ 中统计字符串数组所有元素的“乱序组合”（即全排列），最简洁高效的方式是利用标准库的 std::next_permutation。它会按字典序生成下一个排列，并且能正确处理重复元素（不会生成重复排列），只需提前将数组排序。
            // do
            // {
            //     string key;
            //     for(int i = 0; i < num; i++)
            //     {
            //         key += words[i];
            //     }
            //     map[key]++;
            // }while(next_permutation(words.begin(), words.end()));   // 按字典序生成下一个排序

            unordered_set<string> map;
            sort(words.begin(), words.end());
            do
            {
                string key;
                for(int i = 0; i < num; i++)
                {
                    key += words[i];
                }
                map.insert(key);
            }while(next_permutation(words.begin(), words.end()));

            for(int i = 0; i < s.size(); i++)
            {
                if(i + Length > s.size())
                {
                    break;
                }
                string temp = s.substr(i, Length);
                if(map.find(temp) != map.end())
                {
                    result.push_back(i);
                }
            }
            return result;
        }


        vector<int> findSubstring_DeepSeek(string s, vector<string>& words)
        {
            vector<int> result;
            if (words.empty()) return result;

            int num = words.size();
            int wordLen = words[0].size();
            int totalLen = num * wordLen;

            if (s.size() < totalLen || wordLen == 0) return result;

            // 统计需求
            unordered_map<string, int> need;
            for (const string& w : words) need[w]++;

            // 枚举起始偏移
            for (int offset = 0; offset < wordLen; ++offset) {
                int left = offset;
                int count = 0;                      // 窗口内的有效单词数
                unordered_map<string, int> window;

                // ★ 安全边界：right + wordLen <= s.size()
                for (int right = offset; right + wordLen <= s.size(); right += wordLen) {
                    string word = s.substr(right, wordLen);

                    // 无效单词 → 重置整个窗口
                    if (need.find(word) == need.end()) {
                        window.clear();
                        count = 0;
                        left = right + wordLen;
                        continue;
                    }

                    // 右扩
                    window[word]++;
                    if (window[word] <= need[word]) count++;

                    // 左缩：某个单词次数超了
                    while (window[word] > need[word]) {
                        string leftWord = s.substr(left, wordLen);
                        window[leftWord]--;
                        if (window[leftWord] < need[leftWord]) count--;
                        left += wordLen;
                    }

                    // 匹配成功
                    if (count == num) {
                        result.push_back(left);

                        // 左边界右移一个单词，继续查找
                        string leftWord = s.substr(left, wordLen);
                        window[leftWord]--;
                        if (window[leftWord] < need[leftWord]) count--;
                        left += wordLen;
                    }
                }
            }
            return result;

            
        }
};