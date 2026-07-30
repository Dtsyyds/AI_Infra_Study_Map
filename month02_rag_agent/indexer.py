from typing import Dict, Any
import json
from pathlib import Path
import os
import tempfile
from copy import deepcopy
import math

SCHEMA_VERSION = 1

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

def build_index(
    chunks: list[str],
    embeddings: list[list[float]],
    source: str,
    model_name: str,
    normalized: bool = False,
) -> Dict[str, Any]:
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name 不能为空")

    records = build_records(chunks, embeddings, source=source)

    return {
        "schema_version": SCHEMA_VERSION,
        "model": model_name,
        "dimension": len(embeddings[0]),
        "normalized": normalized,
        "records": records,
    }

# def save_index(
#     index: Dict[str, Any],
#     output_path: str|Path,
# ) -> None:
#     path = Path(output_path)
#     # 将传入的路径参数（字符串或 Path 对象）统一转换为 pathlib.Path 对象，便于后续使用面向对象的路径操作方法。
#     path.parent.mkdir(parents=True, exist_ok=True)
#     # 获取该路径的父目录，并递归创建它

#     with path.open("w", encoding="utf-8") as f:
#         json.dump(
#             index, # 要保存的是 index 对象（即函数接收的第一个参数，类型为 Dict[str, Any]）
#             f, # 文件对象
#             indent=2,
#             ensure_ascii=False, # Python 会保留原始的中文字符,Python 会将所有非 ASCII 字符（如中文）转义为 \uXXXX 的格式
#         )
# 原子化且i具备崩溃安全性的 Json 文件保存机制，核心目的：要么n完整成功的写入新文件，要么在失败时完全不改变原文件，并且不会留下残留的垃圾文件
def save_index(
    index: Dict[str, Any],
    output_path: str|Path,
) -> None:
    # 准备目标路径
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

    try:
        # 临时文件初始化
        with tempfile.NamedTemporaryFile(   # 在指定目录下创建一个临时文件对象
            mode="w",
            encoding="utf-8",
            dir=f"{path.parent}",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)

            json.dump(
                index,
                file,
                indent=2,
                ensure_ascii=False,
            )
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)

    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

def validate_index(index: Dict[str, Any]) -> None:
    if not isinstance(index, dict):
        raise ValueError("索引根节点必须是 JSON 对象")
    version = index.get("schema_version")

    if type(version) is not int:
        raise ValueError(
            f"schema_version 必须是整数: actual={version!r}"
        )

    if version != SCHEMA_VERSION:
        raise ValueError(
            f"不支持的 schema_version: expected={SCHEMA_VERSION}, actual={version!r}"
        )

    model = index.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(
            f"model 必须是非空字符串: actual={model!r}"
        )

    dimension = index.get("dimension")
    if type(dimension) is not int:
        raise ValueError(
            f"dimension 必须是整数: actual={dimension!r}"
        )
    if dimension < 1:
        raise ValueError(
            f"dimension 必须大于 0: actual={dimension!r}"
        )

    seen_ids: set[str] = set()
    normalized = index.get("normalized")

    if type(normalized) is not bool:
        raise ValueError(
            f"normalized 必须是布尔值：actual={normalized!r}"
        )

    records = index.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(
            f"records 必须是非空列表：actual={records!r}"
        )

    for record_index, record in enumerate(records):
        if not isinstance(record, dict) or not record:
            raise ValueError(
                f"record 必须是 JSON 对象："
                f"record_index={record_index}, actual={record!r}"
            ) 

        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(
                f"id 必须是非空字符串："
                f"record_index={record_index}, actual={record_id!r}"
            )

        if record_id in seen_ids:
            raise ValueError(
                f"id 不能重复："
                f"record_index={record_index}, actual={record_id!r}"
            )

        seen_ids.add(record_id)

        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                f"text 必须是非空字符串："
                f"record_index={record_index}, actual={text!r}"
            )

        metadata = record.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            raise ValueError(
                f"metadata 必须是 JSON 对象："
                f"record_index={record_index}, actual={metadata!r}"
            )

        source = metadata.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(
                f"metadata.source 必须是非空字符串："
                f"record_index={record_index}, actual={source!r}"
            )

        chunk_index = metadata.get("chunk_index")
        if type(chunk_index) is not int:
            raise ValueError(
                f"metadata.chunk_index 必须是整数："
                f"record_index={record_index}, actual={chunk_index!r}"
            )

        if chunk_index < 0:
            raise ValueError(
                f"metadata.chunk_index 不能为负数："
                f"record_index={record_index}, actual={chunk_index!r}"
            )

        expected_id = f"{source}::chunk_{chunk_index:04d}"
        if record_id != expected_id:
            raise ValueError(
                f"id 与 metadata 不一致："
                f"record_index={record_index}, expected={expected_id}, actual={record_id!r}"
            )

        embedding = record.get("embedding")

        if not isinstance(embedding, list) or not embedding:
            raise ValueError(
                f"embedding 必须是列表："
                f"record_index={record_index}, actual={embedding!r}"
            )

        actual_dimension = len(embedding)

        if actual_dimension != dimension:
            raise ValueError(
                f"向量维度与索引声明不一致："
                f"expected={dimension}, actual={actual_dimension}, "
                f"record_index={record_index}"
            )

        for value_index, value in enumerate(embedding):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"embedding 元素必须是数值："
                    f"record_index={record_index}, value_index={value_index}, actual={value!r}"
                )

            if not math.isfinite(value):
                raise ValueError(
                    f"embedding 元素必须是有限数值："
                    f"record_index={record_index}, value_index={value_index}, actual={value!r}"
                )

    


def load_index(input_path: str|Path) -> Dict[str, Any]:
    path = Path(input_path)
    with path.open("r", encoding="utf-8") as f:
        index = json.load(f)
    validate_index(index)
    return index

if __name__ == "__main__":

    valid_index = build_index(
        chunks=["第一段", "第二段"],
        embeddings=[
            [0.1, 0.2],
            [0.3, 0.4],
        ],
        source="docs/sandbox.md",
        model_name="demo-embedding-model",
    )

    validate_index(valid_index)

    bad_path = Path("month02_rag_agent/data/bad_header.json")

    # bad_index = dict(valid_index)
    # bad_index["model"] = "   "
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     message = str(error)
    #     assert "model 必须是非空字符串" in message
    #     assert "actual='   '" in message
    # else:
    #     raise AssertionError("预期空白 model 被拒绝")
    # bad_index = dict(valid_index)
    # bad_index["dimension"] = True
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     message = str(error)
    #     assert "dimension 必须是整数" in message
    #     assert "actual=True" in message
    # else:
    #     raise AssertionError("预期布尔类型 dimension 被拒绝")

    # bad_index = dict(valid_index)
    # bad_index["dimension"] = 0
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     message = str(error)
    #     assert "dimension 必须大于 0" in message
    #     assert "actual=0" in message
    # else:
    #     raise AssertionError("预期非正数 dimension 被拒绝")

    # bad_index = deepcopy(valid_index)
    # bad_index["records"] = []
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     assert "records 必须是非空列表" in str(error)
    # else:
    #     raise AssertionError("预期空 records 被拒绝")

    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["embedding"] = "xx"
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     message = str(error)
    #     assert "embedding 必须是列表" in message
    #     assert "record_index=0" in message
    # else:
    #     raise AssertionError("预期非列表 embedding 被拒绝")

    # bad_index = deepcopy(valid_index)
    # bad_index["records"][1]["embedding"] = [0.3]
    # save_index(bad_index, bad_path)

    # try:
    #     load_index(bad_path)
    # except ValueError as error:
    #     message = str(error)
    #     assert "向量维度与索引声明不一致" in message
    #     assert "expected=2" in message
    #     assert "actual=1" in message
    #     assert "record_index=1" in message
    # else:
    #     raise AssertionError("预期错误向量维度被拒绝")

    # # 测试 1：字符串不是数值
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["embedding"] = [0.1, "0.2"]

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "embedding 元素必须是数值" in message
    #     assert "record_index=0" in message
    #     assert "value_index=1" in message
    # else:
    #     raise AssertionError("预期字符串向量元素被拒绝")

    # 测试 2：True 不能被当作数字 1
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["embedding"] = [0.1, True]

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     assert "embedding 元素必须是数值" in str(error)
    # else:
    #     raise AssertionError("预期布尔向量元素被拒绝")

    # # 测试 3：拒绝 NaN 和无穷值
    # for invalid_value in [
    #     float("nan"),
    #     float("inf"),
    #     float("-inf"),
    # ]:
    #     bad_index = deepcopy(valid_index)
    #     bad_index["records"][1]["embedding"] = [0.3, invalid_value]

    #     try:
    #         validate_index(bad_index)
    #     except ValueError as error:
    #         message = str(error)
    #         assert "embedding 元素必须是有限数值" in message
    #         assert "record_index=1" in message
    #         assert "value_index=1" in message
    #     else:
    #         raise AssertionError(
    #             f"预期非有限向量元素被拒绝：{invalid_value!r}"
    #         )

    # print("embedding 元素校验测试通过")

    # 测试 1：空白 id
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["id"] = "   "

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "id 必须是非空字符串" in message
    #     assert "record_index=0" in message
    # else:
    #     raise AssertionError("预期空白 id 被拒绝")

    # # 测试 2：重复 id
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][1]["id"] = bad_index["records"][0]["id"]

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "id 不能重复" in message
    #     assert "record_index=1" in message
    # else:
    #     raise AssertionError("预期重复 id 被拒绝")

    # # 测试 3：空白 text
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][1]["text"] = " "

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "text 必须是非空字符串" in message
    #     assert "record_index=1" in message
    # else:
    #     raise AssertionError("预期空白 text 被拒绝")

    # print("Record id/text 校验测试通过")

    # # # 测试 1：metadata 不是对象
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["metadata"] = []

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "metadata 必须是 JSON 对象" in message
    #     assert "record_index=0" in message
    # else:
    #     raise AssertionError("预期非对象 metadata 被拒绝")

    # # 测试 2：source 为空白字符串
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["metadata"]["source"] = "   "

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     assert "metadata.source 必须是非空字符串" in str(error)
    # else:
    #     raise AssertionError("预期空白 source 被拒绝")

    # # # 测试 3：True 不能作为 chunk_index
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["metadata"]["chunk_index"] = True

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     assert "metadata.chunk_index 必须是整数" in str(error)
    # else:
    #     raise AssertionError("预期布尔 chunk_index 被拒绝")

    # # 测试 4：chunk_index 不能为负数
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][0]["metadata"]["chunk_index"] = -1

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     assert "metadata.chunk_index 不能为负数" in str(error)
    # else:
    #     raise AssertionError("预期负数 chunk_index 被拒绝")
    # 测试 5：id 与 metadata 不一致
    # bad_index = deepcopy(valid_index)
    # bad_index["records"][1]["metadata"]["chunk_index"] = 7

    # try:
    #     validate_index(bad_index)
    # except ValueError as error:
    #     message = str(error)
    #     assert "id 与 metadata 不一致" in message
    #     assert "chunk_0007" in message
    #     assert "record_index=1" in message
    # else:
    #     raise AssertionError("预期不一致的 id 被拒绝")

    # print("metadata/id 一致性校验测试通过")

        # 正向测试：True 是合法布尔值
    true_index = deepcopy(valid_index)
    true_index["normalized"] = True
    validate_index(true_index)

    # 测试 1：缺少 normalized
    bad_index = deepcopy(valid_index)
    bad_index.pop("normalized")

    try:
        validate_index(bad_index)
    except ValueError as error:
        message = str(error)
        assert "normalized 必须是布尔值" in message
        assert "actual=None" in message
    else:
        raise AssertionError("预期缺失 normalized 被拒绝")

    
    # 测试 2：整数 0 不能冒充 False
    bad_index = deepcopy(valid_index)
    bad_index["normalized"] = 0

    try:
        validate_index(bad_index)
    except ValueError as error:
        message = str(error)
        assert "normalized 必须是布尔值" in message
        assert "actual=0" in message
    else:
        raise AssertionError("预期整数 normalized 被拒绝")

    # 测试 3：字符串 false 不是布尔值
    bad_index = deepcopy(valid_index)
    bad_index["normalized"] = "false"

    try:
        validate_index(bad_index)
    except ValueError as error:
        message = str(error)
        assert "normalized 必须是布尔值" in message
        assert "actual='false'" in message
    else:
        raise AssertionError("预期字符串 normalized 被拒绝")

    print("normalized 字段校验测试通过")