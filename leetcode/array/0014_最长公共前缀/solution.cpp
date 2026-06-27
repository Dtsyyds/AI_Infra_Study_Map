# include <vector>
# include <iostream>
# include <algorithm>
# include <string>

using namespace std;

class Solution_Base {
    public:
        string longestCommonPrefix(vector<string>& strs)
        {
            if (strs.empty()) return "";
            string prefix = strs[0];
            for(int i = 0; i < strs.size(); i++)
            {
                prefix = longestCommonPrefix(prefix, strs[i]);
            }
            return prefix;

        }

        string longestCommonPrefix(const string& strs1, const string& strs2)
        {
            int length = min(strs1.size(), strs2.size());

            for (int i = 0; i < length; i++)
            {
                if (strs1[i] != strs2[i])
                    return strs1.substr(0, i);
            }
            return "";
        }
        
};

class Solution_Pro {
    public:
        string longestCommonPrefix(vector<string>& strs)
        {
            if (strs.empty()) return "";
            sort(strs.begin(), strs.end());
            string prefix = strs[0];

            for (int i = 0; i < strs.size(); i++)
            {
                for(int j = 0; j < prefix.size(); j++)
                {
                    if(prefix[j] != strs[i][j])
                    {
                        prefix = prefix.substr(0, j);
                        break;
                    }
                }
            }
            return prefix;
        }
};
