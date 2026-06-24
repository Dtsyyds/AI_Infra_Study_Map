# include <vector>
# include <algorithm>
# include <iostream>

using namespace std;

class Solution_OWN{
    public:
        double findMedianSortedArrays(vector<int> & nums1, vector<int> & nums2)
        {
            int m = nums1.size();
            int n = nums2.size();
            int total = m + n;
            vector<int> num;
            num.reserve(m+n);
            // 将两个数组合并为一个数组
            for(int i = 0; i < m; i++)
            {
                num.push_back(nums1[i]);
            }
            for(int i = 0; i < n; i++)
            {
                num.push_back(nums2[i]);
            }

            sort(num.begin(), num.end());

            if(total % 2 == 0)
            {
                return (num[total / 2] + num[total / 2 - 1]) /  2.0;
            }
            else
            {
                return num[total / 2];
            }

        }
};

class Solution_PRO{
    double findMedianSortedArrays(vector<int> & nums1, vector<int> & nums2)
    {
        vector<int> num;
        int m = nums1.size();
        int n = nums2.size();
        int total = m + n;
        num.reserve(total);

        int i = 0, j = 0;
        // 特殊情况
        if(m == 0)
        {
            for(int i = 0; i < n; i++)
            {
                num.push_back(nums2[i]);
            }
        }
        
        if(n == 0)
        {
            for(int i = 0; i < m; i++)
            {
                num.push_back(nums1[i]);
            }
        }

        while(i < m && j < n)
        {
            if(nums1[i] < nums2[j])
            {
                num.push_back(nums1[i]);
                i++;
                if(i == m)
                {
                    while(j < n)
                    {
                        num.push_back(nums2[j]);
                        j++;
                    }
                }
            }
            else
            {
                num.push_back(nums2[j]);
                j++;
                if(j == n)
                {
                    while(i < m)
                    {
                        num.push_back(nums1[i]);
                        i++;
                    }
                }
            }
        }

        if(total % 2 == 0)
        {
            return (num[total / 2] + num[total / 2 - 1]) /  2.0;
        }
        else
        {
            return num[total / 2];
        }
    }
};

class Solution_Plus {
public:
    double findMedianSortedArrays(vector<int>& nums1, vector<int>& nums2) {
        if(nums1.size() > nums2.size())
        {
            return findMedianSortedArrays(nums2, nums1);
        }

        int m = nums1.size();
        int n = nums2.size();

        int left = 0, right = m;

        int total = m + n;
        int leftSize = (total + 1) / 2;

        while(left <= right)
        {
            int i = (left + right) / 2;
            int j = leftSize - i;

            long long Aleft = (i == 0) ? LLONG_MIN : nums1[i - 1];
            long long Aright = (i == m) ? LLONG_MAX : nums1[i];

            long long Bleft = (j == 0) ? LLONG_MIN : nums2[j - 1];
            long long Bright = (j == n) ? LLONG_MAX : nums2[j];

            if(Aleft <= Bright && Bleft <= Aright)
            {
                if(total % 2 == 1)
                {
                    return max(Aleft, Bleft);
                }
                else
                {
                    return (max(Aleft, Bleft) + min(Aright, Bright)) / 2.0;
                }
            }
            else if(Aleft > Bright)
            {
                right = i - 1;
            }
            else
            {
                left = i + 1;
            }
        }
        return 0.0;

    }
};