from typing import Dict, Any

def build_record(text: str, embedding: list[float], metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": f'{metadata["source"]}::chunk_{metadata["chunk_index"]:04d}',
        "text": text,
        "embedding": embedding,
        "metadata":{
            "source": metadata["source"],
            "chunk_index": metadata["chunk_index"],
        }
    }

def build_records(
    chunks: list[str],
    embeddings: list[list[float]],
    source: str
) -> list[Dict[str, Any]]:
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("chunks 不能为空")
    if not isinstance(embeddings, list) or not embeddings:
        raise ValueError("embeddings 不能为空")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks 和 embeddings 长度不一致")
    # 需要保证同一索引中的向量维度一致
    expected_dimension = len(embeddings[0])

    for index, embedding in enumerate(embeddings):
        if len(embedding) < 1:
                raise ValueError(
                    f"向量不能为空,"
                    f"source={source}, chunk_index={index}")
        if len(embedding) != expected_dimension:
            raise ValueError(
                f"向量维度不一致："
                f"expected={expected_dimension}, actual={len(embedding)},"
                f"source={source}, chunk_index={index},"
                f"text={chunks[index]!r}"
            )

    records = []
    for index, chunk in enumerate(chunks):
        record = build_record(
            text=chunk,
            embedding=embeddings[index],
            metadata={
                "source": source,
                "chunk_index": index,
            },
        )
        records.append(record)

    return records

if __name__ == "__main__":
    try:
        build_records(
            chunks=["第一段", "第二段"],
            embeddings=[[], []],
            source="docs/sandbox.md",
        )
    except ValueError as error:
        message = str(error)
        assert "向量不能为空" in message
        assert "source=docs/sandbox.md" in message
        assert "chunk_index=0" in message
    else:
        raise AssertionError("预期空向量时抛出 ValueError")