# include <iostream>
# include <string>
# include <cctype>
# include <vector>
# include <unordered_map>

using namespace std;

int main()
{
    // 字符串的定义和大小
    string s = "hello";
    int len = s.size();     // size() 返回 size_t 比较时注意强转 (int)， 防止无符号溢出

    for(int i = 0; i < len; i++)
    {
        cout << s[i] << endl;   // 字符串是可变数组，可以直接通过下标修改访问
    }

    // 末尾操作
    s.push_back('!');
    s.pop_back();

    // 字符串拼接
    s += " world";
    string sub = s.substr(0, 5);    // 靠下标截取子串

    // 查找
    int pos = s.find('e');
    int pos1 = s.find("world");

    // 转化
    int num = stoi(s);
    string str = to_string(num);

    isalnum(s[0]);  // 判断是否为字母或数字
    isalpha(s[0]);  // 判断是否为字母
    isdigit(s[0]);  // 判断是否为数字
    tolower(s[0]);  // 转为小写
    toupper(s[0]);  // 转为大写

    // 字符哈希的两种存法（解决异位词类）
    // 数组哈希
    vector<int> hash(26, 0);
    for(char c : s)
    {
        hash[c - 'a']++;    // 利用 ASCII 码的连续性
    }
    
    // 通用哈希表（包含特殊字符）
    unordered_map<char, int> count;
    for(char c : s)
    {
        count[c]++;
    }

    
    return 0;
}