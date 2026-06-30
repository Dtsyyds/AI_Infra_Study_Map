# include <iostream>
# include <string>
# include <vector>
# include <functional>
# include <utility>

using namespace std;
int simple_hash(const string& key, int size)
{
    /*一个简单的字符串哈希函数
    Param:
    key: 字符串
    size: 哈希表的大小一个简单的字符串哈希函数
    Param:
    key: 字符串
    size: 哈希表的大小
    Return:
    计算得到的数组索引*/
    int total = 0;
    for(int i = 0; i < key.size(); i++)
    {
        // 将字符串转化成ASCII码，然后累加到total中
        total += key[i];
    }
    return total % size;

}

// 使用链地址法解决哈希冲突问题
class HashTableChaining
{
    public:
        HashTableChaining(int size)
        {
            this->size = size;
            this->table.resize(size);
        }

        

        void insert(const string& key, const string& value)
        {
            // 向哈希表中插入键值对
            int index = _hash(key);
            auto& bucket = table[index];    // 获取对应索引的桶

            // 遍历桶中的所有键值对，检查是否存在相同的key
            for(auto& pair : bucket)
            {
                if(pair.first == key)
                {
                    // 如果存在，则更新其值
                    pair.second = value;
                    return;
                }
            }
            bucket.emplace_back(key, value);
        }

        string find(const string& key)
        {
            // 根据键查找值
            int index = _hash(key);
            auto& bucket = table[index];

            for(auto& pair : bucket)
            {
                if(pair.first == key)
                {
                    return pair.second;
                }
            }
            return "";
        }

        bool find_deepseek(const string& key, string& value) const
        {
            int index = _hash(key);
            const auto& bucket = table[index];
            for(const auto& kv : bucket)
            {
                if(kv.first == key)
                {
                    value = kv.second;
                    return true;
                }
            }
            return false;
        }

        bool remove(const string& key)
        {
            // 根据键删除值
            int index = _hash(key);
            auto& bucket = table[index];

            for(auto iter = bucket.begin(); iter != bucket.end(); iter++)
            {
                if(iter->first == key)
                {
                    bucket.erase(iter);
                    return true;
                }
            }
            return false;
        }

        void display()
        {
            // 显示哈希表的内容
            for(int i = 0; i < size; i++)
            {
                cout << "桶" << i << ": ";
                for(auto& pair : table[i])
                {
                    cout << "(" << pair.first << ", " << pair.second << ") ";
                }
                cout << endl;
            }
        }

    private:
        int size;
        vector<vector<pair<string, string>>> table;

        /*
        * 内部哈希函数，使用C++标准库中的hash
        * @param key: 待哈希的字符串
        * @param size: 哈希表的大小
        * @return: 计算得到的数据索引

        */
        int _hash(const string& key) const
        {
            return static_cast<int>(hash<string>{}(key) % size);
        }
};

// 使用线性开放地址法解决哈希冲突
class HashTableOpenAddressing
{
    public:
        HashTableOpenAddressing(int size)
        {
            this->size = size;
            this->keys.resize(size);
            this->values.resize(size);
        }

        void insert(const string& key, const string& value)
        {
            int index = _hash(key);
            int original_index = index;

            // 线性探测寻找空位或相同的键
            while(keys[index] != "")
            {
                if(keys[index] == key)
                {
                    // 键已存在，更新值
                    values[index] = value;
                    return;
                }
                index = (index + 1) % size;     // 移动到下一个位置，循环数组
                if(index == original_index)
                {
                    // 循环找了一圈，表满了最好是扩容
                    cout << "表已满" <<endl;
                    break;
                }
            }
            // 找到空位置，插入
            keys[index] = key;
            values[index] = value;
        }

        string find(const string& key)
        {
            int index = _hash(key);
            int original_index = index;

            while(keys[index] != "")
            {
                if(keys[index] == key)
                {
                    return values[index];
                }
                index = (index + 1) % size;
                if(index == original_index)
                {
                    break;
                }
            }
            return "";
        }

        void display()
        {
            for(int i = 0; i < size; i++)
            {
                if(keys[i] != "")
                {
                    cout<<"索引是："<<i<<"键是："<<keys[i]<<"值是："<< values[i]<<endl;
                }
                else
                {
                    cout<<"索引"<<i<<"为空"<<endl;
                }
            }
        }


    private:
        int size;
        vector<string> keys;
        vector<string> values;

        int _hash(const string& key) const
        {
            return static_cast<int>(hash<string>{}(key) % size);
        }
};

vector<string> keys_to_test = {"hello", "world", "hash_table"};

int main()
{
    // int size = 12;
    // for(int i = 0; i < keys_to_test.size(); i++)
    // {
    //     int index = simple_hash(keys_to_test[i], size);
    //     cout << " 键"<< keys_to_test[i] << "的哈希值是" << index << endl;
    // }
    // return 0;

    HashTableChaining hashTable(12);
    hashTable.insert("Alice", "25");
    hashTable.insert("Bob", "30");
    hashTable.insert("Alice", "26");   // 更新 Alice 的值

    string age;
    if(hashTable.find("Alice") != "")
    {
        cout << "Alice 的年龄是: " << hashTable.find("Alice") << endl;
    }
    hashTable.display();
    hashTable.remove("Alice");
    hashTable.display();

}