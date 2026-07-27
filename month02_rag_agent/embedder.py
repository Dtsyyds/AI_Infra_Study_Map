"""
embedder.py

生成真实向量
把文本转化为同一语义空间，同一归一化策略下的向量

"""

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章"

class LocalEmbedder:
    """本地 Embedding 模型封装"""
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME,) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("模型名称不能为空")
        self.model_name = model_name
        # 模型只在创建 Embedder 加载一次
        self.model = SentenceTransformer(self.model_name)
        
    @staticmethod
    def _validate_text(text: str, field_name: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{field_name}不能为空")
        
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量生成文档 Chunk 的向量"""
        if not isinstance(texts, list) or not texts:
            raise ValueError("texts 不能为空")

        for index, text in enumerate(texts):
            self._validate_text(text, f"texts[{index}]")

        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False,)
        # 这样i处理，模型一次接受一批文本，内部按 Batch 推理
        """
        # 不推荐：模型推理 N 次
        vectors = []
        for text in texts:
            vectors.append(self.model.encode(text))

        每个 chunk 都单独经过一次模型推理流程，CPU/GPU 无法充分批处理，框架调度开销也被重复 N 次
        """
        # ndarray -> 原始 Python float, 后续才能 Json 序列化
        # return [
        #     [float(x) for x in embedding]
        #     for embedding in embeddings
        # ]
        return embeddings.tolist()
        # 更直接 语气更清晰
        """
        return embeddings.tolist()
        """

    # BGE 检索模型训练时定义的非对称输入协议：同一个模型、同一套训练约定下，查询加 instruction、文档不加
    """
    向量维度相同 ≠ “可以比较
    同一模型 + 同一版本 + 同一预处理协议 才能可信比较
    """
    def embed_query(self, query: str) -> list[float]:
        """生成查询向量：只给 query 加 instruction"""
        self._validate_text(query, "query")

        query_with_instruction = QUERY_INSTRUCTION + query

        embedding = self.model.encode(
            query_with_instruction,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        return [float(value) for value in embedding]
        # 更直接 语气更清晰
        """
        return embedding.tolist()
        """
