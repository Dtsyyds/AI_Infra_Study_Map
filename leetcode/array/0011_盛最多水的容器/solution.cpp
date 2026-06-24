# include <vector>
# include <algorithm>

using namespace std;

class Solution_TimeOut {
    public:
        int maxArea(vector<int>& height)
        {
            int n = height.size();
            int Max_Area = 0, Cur_Area = 0;
            for(int i = 0; i < n; i++)
            {
                for(int j = i + 1; j < n; j++)
                {
                    Cur_Area = (j - i) * min(height[i], height[j]);
                    if(Cur_Area > Max_Area)
                        Max_Area = Cur_Area;
                }
            }
            return Max_Area;
        }
};

class Solution_Try{
    public:
        int maxArea(vector<int>& height)
        {
            int n = height.size();
            int j = n / 2;
            int i = n / 2 - 1;
            int Max_Area = (j - i) * min(height[i], height[j]);
            int Cur_Area = 0;
            while(i > 0 || j < n - 1)
            {
                if(i == 0 && j < n - 1)
                {
                    j++;
                    Cur_Area = (j - i) * min(height[i], height[j]);
                    if(Cur_Area > Max_Area)
                        Max_Area = Cur_Area;
                    continue;
                }
                if(j == n - 1 && i > 0)
                {
                    i --;
                    Cur_Area = (j - i) * min(height[i], height[j]);
                    if(Cur_Area > Max_Area)
                        Max_Area = Cur_Area;
                    continue;
                }
                if(height[i] < height[j])
                {
                    i --;
                    Cur_Area = (j - i) * min(height[i], height[j]);
                    if(Cur_Area > Max_Area)
                        Max_Area = Cur_Area;
                }
                else
                {
                    j ++;
                    Cur_Area = (j - i) * min(height[i], height[j]);
                    if(Cur_Area > Max_Area)
                        Max_Area = Cur_Area;
                }
            }
            return Max_Area;
        }

};

class Solution_DeepSeek {
public:
    int maxArea(vector<int>& height) {
        int n = height.size();
        int i = 0, j = n - 1;
        int Max_Area = 0;
        int Cur_Area = 0;
        while(i < j)
        {
            if(height[i] < height[j])
            {
                Cur_Area = (j - i) * height[i];
                i++;
                Max_Area = max(Max_Area, Cur_Area);
            }
            else
            {
                Cur_Area = (j - i) * height[j];
                j--;
                Max_Area = max(Max_Area, Cur_Area);
            }
        }
        return Max_Area;
    }
};