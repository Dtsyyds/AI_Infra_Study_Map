"""
retriever.py

实现余弦相似度
"""
from collections.abc import Sequence

def cosine_similarity(vector_a: Sequence[float], vector_b: Sequence[float],) -> float:
    """
    计算两个向量的余弦相似度
    """
    if not vector_a or not vector_b:
        raise ValueError("向量不能为空")
    if len(vector_a) != len(vector_b):
        raise ValueError("两个向量的长度必须相同")
    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    magnitude_a = sum(a * a for a in vector_a) ** 0.5
    magnitude_b = sum(b * b for b in vector_b) ** 0.5
    if magnitude_a == 0 or magnitude_b == 0:
        raise ValueError("向量的模不能为0")
    return dot_product / (magnitude_a * magnitude_b)

def search(query_vector: Sequence[float], records: list[dict], top_k: int = 3, min_score=None) -> list[dict]:
    """
    根据查询向量，在记录中搜索最相似的 top_k 条记录
    
    Args:
        query_vector (Sequence[float]): 查询向量
        records (list[dict]): 记录列表，每个记录包含一个向量和一个唯一标识符
        top_k (int): 要返回的相似度最高的记录数
    Returns:
        list[dict]: 最相似的 top_k 条记录列表
    """
    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    if min_score is not None:
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
            raise ValueError("min_score 必须是数字")
        if min_score < 0 or min_score > 1:
            raise ValueError("min_score 的值必须在 [0, 1] 之间")
    
    if not records:
        return []
    
    scores = []
    for record in records:
        similarity = cosine_similarity(query_vector, record['embedding'])
        if min_score is not None and similarity < min_score:
            continue
        scores.append((record, similarity))
    scores.sort(key=lambda x: x[1], reverse=True)

    result = []
    
    top_k = min(top_k, len(scores))
    for i in range(top_k):
        # print(f"{i+1}. {scores[i][0]['text']} - 相似度: {scores[i][1]:.4f}")
        record, sim = scores[i]
        result.append({
            "id": record.get('id',""),
            "text": record.get('text', ""),
            "metadata": record.get('metadata', {}),
            "score": sim,
        })
        # for scores[i] in scores:
            # print(x)
    
    return result

if __name__ == "__main__":
    records = [
    {
        "id": "runtime",
        "text": "Agent Runtime 负责循环调度",
        "embedding": [0.9, 0.1, 0.0, 0.0],
        "metadata": {},
    },
    {
        "id": "sandbox",
        "text": "沙盒负责限制文件访问范围",
        "embedding": [0.1, 0.9, 0.0, 0.0],
        "metadata": {},
    },
    {
        "id": "trace",
        "text": "Trace 记录 Agent 执行轨迹",
        "embedding": [0.0, 0.1, 0.9, 0.0],
        "metadata": {},
    },
    {
        "id": "memory",
        "text": "Memory 保存任务上下文",
        "embedding": [0.0, 0.0, 0.1, 0.9],
        "metadata": {},
    },
]

    query_vector = [0.2, 0.95, 0.0, 0.0]
    print(search(query_vector, records))

