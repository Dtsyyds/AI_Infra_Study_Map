import json
import pytest
from month02_rag_agent.app import build_document_index, main
from pathlib import Path

class FakeEmbedder:

    def __init__(self, model_name="fake-embedder-v1", normalized=True):
        self.model_name = model_name
        self.normalized = normalized
        self.received_texts = []

    def embed_documents(self, texts):
        self.received_texts = list(texts)

        return [
            [1.0, 0.0]
            for _ in self.received_texts
        ]

def test_build_document_index_returns_dict_and_writes_json(tmp_path,):
    # input_path = tmp_path / "document.md" 因为沙箱文件安全权限，不会读取沙盒外的文件
    input_path = (
        Path(__file__).parent
        / "fixtures"
        / "document.md"
    )
    output_path = tmp_path / "index.json"

    # input_path.write_text(
    #     "你是一个简单的 agent rag 系统",
    #     encoding="utf-8"
    # )

    fake_embedder = FakeEmbedder()
    # 只返回一个索引字典，不是多个结果
    result = build_document_index(
        input_path=str(input_path),
        output_path=str(output_path),
        embedder=fake_embedder,
        chunk_size=100,
        overlap=20
    )

    assert isinstance(result, dict)
    assert output_path.exists()

    saved_index = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved_index == result

def test_build_document_index_uses_embedder_metadata(tmp_path):
    input_path = (
        Path(__file__).parent
        / "fixtures"
        / "document.md"
    )
    output_path = tmp_path / "index.json"
    fake_embedder = FakeEmbedder(model_name="test-model", normalized=False)

    result = build_document_index(
        input_path=str(input_path),
        output_path=str(output_path),
        embedder=fake_embedder,
        chunk_size=100,
        overlap=20
    )
    saved_index = json.loads(output_path.read_text(encoding="utf-8"))

    assert saved_index["model"] == "test-model"
    assert saved_index["normalized"] is False
    assert result["model"] == "test-model"
    assert result["normalized"] is False

def test_build_document_index_raises_when_input_missing(tmp_path):
    input_path = (
        Path(__file__).parent
        / "fixtures"
        / "missing.md"
    )
    output_path = tmp_path / "index.json"

    fake_embedder = FakeEmbedder()

    with pytest.raises(FileNotFoundError):
        build_document_index(
            input_path=str(input_path),
            output_path=str(output_path),
            embedder=fake_embedder,
            chunk_size=100,
            overlap=20
        )

    assert not output_path.exists()

def test_main_returns_one_when_input_missing(tmp_path, capsys):
    input_path = (
            Path(__file__).parent
            / "fixtures"
            / "missing.md"
        )
    output_path = tmp_path / "index.json"

    exit_code = main(
        [
            str(input_path),
            str(output_path),
            "--chunk-size", "100",
            "--overlap", "20"
        ],
        embedder_factory=FakeEmbedder,
    )

    captured = capsys.readouterr()  # capsys.readouterr() 捕获标准输出和错误
    assert exit_code == 1
    assert "读取失败: 文件不存在" in captured.err
    assert not output_path.exists()


def test_main_uses_injected_embedder_factory(tmp_path, capsys):
    input_path = (
        Path(__file__).parent
        / "fixtures"
        / "document.md"
    )
    output_path = tmp_path / "index.json"

    fake_embedder = FakeEmbedder(model_name="injected-model", normalized=True)

    factory_calls = []

    def fake_factory():
        factory_calls.append("called")
        return fake_embedder

    exit_code = main(
        [
            str(input_path),
            str(output_path),
            "--chunk-size", "100",
            "--overlap", "20"
        ],
        embedder_factory=fake_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory_calls == ["called"]
    assert output_path.exists()

    saved_index = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved_index["model"] == "injected-model"
    assert saved_index["normalized"] is True
    assert captured.err == ""  # 确保没有错误输出
