# include <iostream>
# include <vector>
# include <unordered_map>
# include <algorithm>

using namespace std;

int main()
{
    // int n;      // n 是数组的长度
    // unordered_map<int, int> map;
    // for(int i = 0; i < n; i++)
    // {
    //     // 当前元素是什么
    //     // 我要查什么
    //     // 查到了怎么处理
    //     // 没查到怎么更新

    // }
    return 0;
}

/*
例题1 两数之和

当前元素：数组中的值
查：哈希表中是否有与数组中值匹配要求的数字
如果有 返回下标
如果没有 将该元素存入哈希表中

*/
class Solution_0001
{
    public:
        vector<int> twoSum(vector<int>& nums, int target)
        {
            unordered_map<int, int> hashtable;
            for(int i = 0; i < nums.size(); i++)
            {
                auto it = hashtable.find(target - nums[i]);

                if(it != hashtable.end())
                {
                    return {i, it->second};
                }
                hashtable[nums[i]] = i;     // 将当前遍历到的数组元素 nums[i] 作为键，将其下标作为值存入哈希表
                // hashtable[i] = nums[i];  // 这样写恰恰相反，需要和之前遍历的一一对应
            }
            return {};
        }
};

/*
例题2 多数元素

当前元素：数组中的值
查：哈希表中是否有与当前元素想等的数字
如果有 累加
如果没有 将元素存入哈希表中

思路：
挨个元素遍历：
变量： 记录当前元素是否已经遍历过了，哈希表
如果没有
for 循环遍历
如果有 break
记录次数，超过 n/2 则返回当前元素
*/

class Solution_0169 {
public:
    int majorityElement(vector<int>& nums) {
        if(nums.size() == 1)
        {
            return nums[0];
        }
        unordered_map<int, int> hashtable;
        for(int i = 0; i < nums.size(); i++)
        {
            int num;
            auto it = hashtable.find(nums[i]);
            if(it != hashtable.end())
            {
                continue;
            }
            else
            {
                for(int j = i + 1; j < nums.size(); j++)
                {
                    if(nums[i] == nums[j])
                    {
                        num++;
                        if(num > (nums.size() / 2))
                        {
                            return nums[i];
                        }
                    }
                }
            }
        }
        return 0;
        
    }
};