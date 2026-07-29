import pytest
import json

from month02_rag_agent.indexer import build_index, validate_index, save_index, load_index

@pytest.fixture
# 测试 1 和测试 2 都使用新的 valid_index,避免某个测试i修改数据后污染其他测试
def valid_index():
    """每个测试都获得一个全新的合法索引"""
    return build_index(
        chunks=["第一段", "第二段"],
        embeddings=[
            [0.1, 0.2],
            [0.3, 0.4],
        ],
        source="docs/sandbox.md",
        model_name="demo-embedding-model",
    )

@pytest.mark.parametrize("normalized", [False, True])
# 会把同一个测试用例执行两次，一次是False,一次是True
def test_validate_index_accepts_boolean_normalized(valid_index, normalized):
    """测试 validate_index 函数接受布尔值 'normalized'"""
    valid_index["normalized"] = normalized
    result = validate_index(valid_index)
    assert result is None

@pytest.mark.parametrize(("invalid_value", "expected_actual"),
                         [
                             (0, "actual=0"),
                             (1, "actual=1"),
                             ("false", "actual='false'"),
                             (None, "actual=None"),
                         ],)
def test_validate_index_rejects_non_boolean_normalized(
    valid_index,
    invalid_value,
    expected_actual,
):
    valid_index["normalized"] = invalid_value

    with pytest.raises(
        ValueError,
        match="normalized 必须是布尔值",
    ) as exc_info:
        validate_index(valid_index)

    assert expected_actual in str(exc_info.value)

def test_validate_index_rejects_missing_normalized(valid_index):
    valid_index.pop("normalized")

    with pytest.raises(
        ValueError,
        match="normalized 必须是布尔值",
    ) as exc_info:
        validate_index(valid_index)

    assert "actual=None" in str(exc_info.value)

def test_validate_index_accepts_non_empty_model(valid_index):
    valid_index["model"] = "demo-embedding-model-v2"
    result = validate_index(valid_index)
    assert result is None

@pytest.mark.parametrize(
    ("invalid_model", "expected_actual"),
    [
        ("", "actual=''" ),
        ("  ", "actual='  '"),
        (123, "actual=123"),
        (None, "actual=None"),
        (True, "actual=True"),
    ],
    ids=["empty", "blank", "integer", "null", "boolean"],
)
def test_validate_index_rejects_invalid_model(
    valid_index,
    invalid_model,
    expected_actual,
):
    valid_index["model"] = invalid_model
    with pytest.raises(
        ValueError,
        match="model 必须是非空字符串",
    ) as exc_info:
        validate_index(valid_index)

    assert expected_actual in str(exc_info.value)

def test_validate_index_rejects_missing_model(valid_index):
    valid_index.pop("model")

    with pytest.raises(
        ValueError,
        match="model 必须是非空字符串",
    ) as exc_info:
        validate_index(valid_index)
    assert "actual=None" in str(exc_info.value)

@pytest.mark.parametrize(
    ("invalid_dimension", "expected_actual"),
    [
        (0, "actual=0"),
        (-1, "actual=-1"),
        (True, "actual=True"),
        (2.5, "actual=2.5"),
        ("2", "actual='2'"),
        (None, "actual=None"),
    ],
    ids=[
        "zero","negative","boolean","float","string","null",
    ],
)
def test_validate_index_rejects_invalid_dimension(
    valid_index,
    invalid_dimension,
    expected_actual,
):
    valid_index["dimension"] = invalid_dimension

    with pytest.raises(
        ValueError,
        match="dimension",
    ) as exc_info:
        validate_index(valid_index)
    assert expected_actual in str(exc_info.value)

def test_validate_index_rejects_missing_dimension(valid_index):
    valid_index.pop("dimension")

    with pytest.raises(ValueError, match="dimension"):
        validate_index(valid_index)

"""
tmp_path 为测试创建独立临时目录；
save_index() 能自动创建嵌套目录；
索引确实写入文件；
load_index() 能读取并通过结构校验；
保存前后的数据完全一致；
测试结束后不会污染项目的 data/ 目录
"""
def test_save_and_load_index(valid_index, tmp_path):
    output_path = tmp_path / "nested" / "index.json"

    save_index(valid_index, output_path)
    loaded_index = load_index(output_path)

    assert output_path.is_file()
    assert loaded_index == valid_index

def test_load_index_rejects_malformed_json(tmp_path):
    index_path = tmp_path / "broken.json"
    index_path.write_text(
        '{"schema_version": 1,',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_index(index_path)

def test_load_index_rejects_invalid_index_contract(
        valid_index,
        tmp_path,
):
    invalid_index = dict(valid_index)
    invalid_index["dimension"] = 0

    index_path = tmp_path / "invalid_index.json"
    index_path.write_text(
        json.dumps(invalid_index, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="dimension",
    ):
        load_index(index_path)