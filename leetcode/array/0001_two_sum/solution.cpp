# include <vector>

using namespace std;

// 自己写的暴力解法
class Solution_OWN {
    public:
        vector<int> twoSum(vector<int> &nums, int target)
        {
            for(int i = 0; i < nums.size(); i++)
            {
                for(int j = i + 1; j < nums.size(); j++)
                {
                    if(nums[i] + nums[j] == target)
                    {
                        return {i, j};
                    }
                }
            }
            return {};
        }
};

// 题解给出的哈希表
# include <unordered_map>

class Solution_Pro{
    public:
        vector<int> twoSum(vector<int> & nums, int target)
        {
            unordered_map<int, int> hastable;
            for(int i = 0; i < nums.size(); i++)
            {
                auto it = hastable.find(target - nums[i]);
                if(it != hastable.end())
                {
                    return {it->second, i};
                }
                hastable[nums[i]] = i;
            }
            return {};
        }
};
