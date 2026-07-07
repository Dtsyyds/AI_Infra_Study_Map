# include <iostream>
# include <unordered_set>

using namespace std;

struct ListNode {
    int val;
    ListNode *next;
    ListNode(int x) : val(x), next(NULL) {}
};

class Solution_HashTable
{
    public:
        bool hasCycle(ListNode *head)
        {
            if(head == NULL || head -> next == NULL)
            {
                return false;
            }

            unordered_set<ListNode*> visited;       // 利用一个无序的哈希表集合，里面存放节点的内存地址，记录已经走过的节点

            while(head != NULL && head -> next != NULL)
            {
                if(visited.count(head))             // 在哈希集合中用于统计某个元素的出现次数，返回值是 0 或 1
                {
                    return true;
                }
                visited.insert(head);
                head = head -> next;
            }
            return false;

        }
};