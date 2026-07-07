# include <iostream>
# include <vector>
# include <unordered_map>
# include <unordered_set>
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
    vector<int> nums = {2, 7, 11, 15};
    int target = 9;
    // 场景1： unordered_map 映射（键->值）
    unordered_map<int, int> map;
    for(int i = 0; i < nums.size(); i++)
    {
        int current = nums[i];
        // 1. 我要查什么
        int targetKey = target - current;
        auto it = map.find(targetKey);

        // 2. 查到了怎么处理
        if(it != map.end())
        {
            return {it->second, i};
        }

        // 3. 没查到怎么更新
        map[current] = i;
    }

    // 场景2： unordered_set 集合（只存“有没有来过”）
    unordered_set<int> set;
    for(int num : nums)
    {
        // 查看当前元素在不在集合里
        if(set.count(num))
        {
            // 在的话说明重复了，做处理
        }
        // 不在的话就存入
        set.insert(num);
    }

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