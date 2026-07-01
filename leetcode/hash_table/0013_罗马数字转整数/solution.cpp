# include <iostream>
# include <vector>

using namespace std;

class Solution{
    public:
        int romanToInt(string s)
        {
            vector<pair<int, string>> roman = {
                {1000, "M"}, {900, "CM"}, {500, "D"}, {400, "CD"}, {100, "C"}, {90, "XC"},
                {50, "L"}, {40, "XL"}, {10, "X"}, {9, "IX"}, {5, "V"}, {4, "IV"}, {1, "I"}
            };

            int answer = 0;
            for(auto & p : roman)
            {
                while(s.find(p.second) == 0)
                {
                    answer += p.first;
                    s.erase(0, p.second.size());
                }
            }
            return answer;
        }

};

int main()
{
    Solution s;
    cout << s.romanToInt("LVIII") << endl;
    return 0;
}