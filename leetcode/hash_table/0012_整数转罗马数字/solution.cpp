# include <iostream>
# include <string>
# include <unordered_map>
# include <math.h>
# include <algorithm>
# include <vector>
# include <map>


using namespace std;

class Solution{
    public:
        string intToRoman(int num)
        {
            string roman = to_string(num);
            unordered_map<int, string> map = {{1, "I"}, {2, "II"}, {3, "III"}, {4, "IV"}, {5, "V"}, {6, "VI"}, {7, "VII"}, {8, "VIII"}, {9, "IX"}, 
            {10, "X"}, {20, "XX"}, {30, "XXX"}, {40, "XL"}, {50, "L"}, {60, "LX"}, {70, "LXX"}, {80, "LXXX"}, {90, "XC"}, 
            {100, "C"}, {200, "CC"}, {300, "CCC"}, {400, "CD"}, {500, "D"}, {600, "DC"}, {700, "DCC"}, {800, "DCCC"}, {900, "CM"}, 
            {1000, "M"}, {2000, "MM"}, {3000, "MMM"}};

            int level = roman.size();
            string answer = "";
            int base = 0;
            while(base < level)
            {
                int dight = int(roman[base] - '0') * pow(10, level - 1 - base);
                if(dight > 0)
                {
                    answer += map[dight];
                    // cout << map[dight] << " "<< dight << endl;
                }
                base++;
            }
            
            // reverse(answer.begin(), answer.end());
            // cout << answer;
            return answer;

        }

        string intToRoman_DeepSeek(int num)
        {
            vector<pair<int, string>> roman = {
                {1000, "M"}, {900, "CM"}, {500, "D"}, {400, "CD"}, {100, "C"}, {90, "XC"},
                {50, "L"}, {40, "XL"}, {10, "X"}, {9, "IX"}, {5, "V"}, {4, "IV"}, {1, "I"}
            };
            string answer = "";
            for(auto &p : roman)
            {
                while(num >= p.first)
                {
                    answer += p.second;
                    num -= p.first;
                }
            }
            return answer;
        }
};

int main()
{
    Solution s;
    string answer;
    answer = s.intToRoman(3749);
    cout << answer;
    return 0;
}