import argparse
import sys
from month02_rag_agent.embedder import LocalEmbedder
from month02_rag_agent.indexer import save_index, build_index
from month02_rag_agent.ingest import load_text_file, split_text

# 应用编排层：加载 -> 切片 -> 向量化 -> 建立索引 -> 保存 -> 返回
def build_document_index(
        input_path: str,
        output_path: str,
        *,
        chunk_size: int,
        overlap: int,
        embedder,
) -> dict:
    document = load_text_file(input_path)
    chunks = split_text(document["text"], chunk_size=chunk_size, chunk_overlap=overlap)
    embeddings = embedder.embed_documents(chunks)

    index = build_index(
        chunks=chunks,
        embeddings=embeddings,
        source=document["metadata"]["source_path"],
        model_name=embedder.model_name,
        normalized=embedder.normalized
    )

    save_index(index=index, output_path=output_path)
    return index


def main(
        argv: list[str] | None = None,
        *,
        embedder_factory=LocalEmbedder,
) -> int:
        parser = argparse.ArgumentParser(description="构建文档索引")
        parser.add_argument("input_path", type=str, help="输入文档路径")
        parser.add_argument("output_path", type=str, help="输出索引路径")
        parser.add_argument("--chunk-size", type=int, default=200, help="文本切片大小")
        parser.add_argument("--overlap", type=int, default=40, help="文本切片重叠大小")

        args = parser.parse_args(argv)

        try:
              embedder = embedder_factory()
              build_document_index(
                    input_path=args.input_path,
                    output_path=args.output_path,
                    chunk_size=args.chunk_size,
                    overlap=args.overlap,
                    embedder=embedder,
              )

        except Exception as e:
            print(f"构建索引失败: {e}", file=sys.stderr)
            return 1

        return 0

if __name__ == "__main__":
      raise SystemExit(main())