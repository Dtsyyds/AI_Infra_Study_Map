# include <iostream>
# include <unordered_map>
# include <string>

using namespace std;

class Solution {
public:
    int lengthOfLongestSubstring(string s) {
        unordered_map<char, int> hastable;
        int max_len = 0;
        int cur_len = 0;
        for(int i = 0; i < s.size(); i++)
        {
            auto it = hastable.find(s[i]);
            if(it != hastable.end())
            {
                cur_len = 1;
                continue;
            }
            else
            {
                cur_len++;
                hastable[s[i]] = i;
                if(cur_len > max_len)
                {
                    max_len = cur_len;
                }
            }
        }
        for(auto it = hastable.begin(); it != hastable.end(); it++)
        {
            cout << it->first << " " << it->second << endl;
        }
        
        return max_len;
    }
};

int main()
{
    Solution s;
    cout << s.lengthOfLongestSubstring("pwwkew") << endl;
    return 0;
}