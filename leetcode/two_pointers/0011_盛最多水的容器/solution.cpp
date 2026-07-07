# include <iostream>
# include <vector>
# include <algorithm>

using namespace std;

class Solution
{
    public:
        int maxArea(vector<int>& height)
        {
            int max_area = 0; 
            for(int left = 0, right = height.size() - 1; left < right; )
            {
                int area = min(height[left], height[right]) * (right - left);
                max_area = max(max_area, area);
                if(height[left] < height[right])
                {
                    left++;
                }
                else
                {
                    right--;
                }
            }
        }
};