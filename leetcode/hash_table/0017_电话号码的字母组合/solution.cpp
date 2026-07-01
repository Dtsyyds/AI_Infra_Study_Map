# include <iostream>
# include <vector>
# include <string>

using namespace std;

class Solution {
    public:
        vector<string> letterCombinations(string digits){
            vector<string> result;

            if(digits.empty())
            {
                return result;
            }
            vector<pair<char, char>> map = {
                {'2', 'a'}, {'2', 'b'}, {'2', 'c'},
                {'3', 'd'}, {'3', 'e'}, {'3', 'f'},
                {'4', 'g'}, {'4', 'h'}, {'4', 'i'},
                {'5', 'j'}, {'5', 'k'}, {'5', 'l'},
                {'6', 'm'}, {'6', 'n'}, {'6', 'o'},
                {'7', 'p'}, {'7', 'q'}, {'7', 'r'}, {'7', 's'},
                {'8', 't'}, {'8', 'u'}, {'8', 'v'},
                {'9', 'w'}, {'9', 'x'}, {'9', 'y'}, {'9', 'z'}

            };

            for(int i = 0; i < digits.size(); i++)
            {
                vector<string> next_result;
                for(int j = 0; j < map.size(); j++)
                {
                    if(digits[i] == map[j].first)
                    {
                        if(result.empty())
                        {
                            next_result.push_back(string(1, map[j].second));
                        }
                        else
                        {
                            int size = result.size();
                            for(int k = 0; k < size; k++)
                            {
                                string temp = result[k];
                                temp.push_back(map[j].second);
                                next_result.push_back(temp);
                            }
                        }
                    }
                    
                }
                result = next_result;
            }
            

            return result;
        }

        vector<string> letterCombinations_ChatGpt(string digits)
        {
            vector<string> result;
            if(digits.empty())
            {
                return result;
            }
            
            vector<string> mapping = {
                "",     // 0
                "",     // 1
                "abc",  // 2
                "def",  // 3
                "ghi",  // 4
                "jkl",  // 5
                "mno",  // 6
                "pqrs", // 7
                "tuv",  // 8
                "wxyz"  // 9
            };

            result.push_back(""); // Start with an empty string

            for(int i = 0; i < digits.size(); i++)
            {
                int digit =  digits[i] - '0';
                string letters = mapping[digit];
                vector<string> next_result;

                for(int j = 0; j < result.size(); j++)
                {
                    for(int k = 0; k < letters.size(); k++)
                    {
                        next_result.push_back(result[j] + letters[k]);
                    }
                }
                result = next_result;
            }
            return result;
        }

};

int main()
{
    Solution s;
    string digits = "23";
    vector<string> result = s.letterCombinations(digits);
    for(int i = 0; i < result.size(); i++)
    {
        cout << result[i] << endl;
    }
}