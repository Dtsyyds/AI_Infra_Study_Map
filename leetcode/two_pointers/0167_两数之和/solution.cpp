# include <iostream>
# include <vector>

using namespace std;

class Solution
{
    public:
        vector<int> twoSum(vector<int>& nums, int target)
        {
            int left = 0;
            int right = nums.size() - 1;
            while(left < right)
            {
                if(nums[left] + nums[right] == target)
                {
                    return {left, right};
                }
                else if(nums[left] + nums[right] < target)
                {
                    left++;
                }
                else
                {
                    right--;
                }
            }
            return {};
        }
};
