"""
ingest.py

初步实现文档加载器功能
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
from month01_agent_loop.tools import resolve_workspace_path

# 当前项目根目录：AI_Infra_Study_Map
# ingest.py 位于 month02_rag_agent/ingest.py
WORKSPACE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..')
)

# 第一版只支持读取 .txt 和 .md 文件
AVAILABLE_FILE = {
    ".txt",
    ".md",
}

def load_text_file(path: str) -> dict:
    """
    加载文本文件，并最终以字典的形式返回
    
    Args:
        path (str): 文件路径
    Returns:
        dict: 包含文件内容的字典
        {
            "text": "文件内容",
            "metadata": {
                "source_path": "文件路径",
                "file_name": "文件名"
            }
        }
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不能为空")
    try:
        ok, abs_path, error = resolve_workspace_path(path)
        if not ok:
            # return f"读取失败：{error}"
            raise ValueError(f"读取失败：{error}")
        
        if not abs_path:
            # return f"读取失败: path 不能为空"
            raise ValueError(f"读取失败: path 不能为空")
        
        if not os.path.exists(abs_path):
            # return f"读取失败: 文件不存在: {abs_path}"
            raise ValueError(f"读取失败: 文件不存在: {abs_path}")
        
        if os.path.isdir(abs_path):
            # return f"读取失败：当前路径是目录，不是文件: {abs_path}"
            raise ValueError(f"读取失败：当前路径是目录，不是文件: {abs_path}")
        
        if not os.path.splitext(abs_path)[1] in AVAILABLE_FILE:
            # return f"读取失败：不支持的文件类型: {abs_path}"
            raise ValueError(f"读取失败：不支持的文件类型: {abs_path}")
        
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
            if not text:
                # return f"读取失败：文件内容为空: {abs_path}"
                raise ValueError(f"读取失败：文件内容为空: {abs_path}")
        relative_path = os.path.relpath(abs_path, WORKSPACE_ROOT)
        """
        假设:
            WORKSPACE_ROOT=/home/dts/AI_Infra_Study_Map
        结果是:
            {
                "source": "month02_rag_agent/docs/agent_infra.md",
                "filename": "agent_infra.md",
            }
        """
        return {
            "text": text,
            "metadata": {
                "source_path": relative_path.replace(os.sep, "/"),
                "file_name": os.path.basename(abs_path),
            }
        }
    
    
    except Exception as e:
        # return f"读取失败: {e}"
        raise ValueError(f"读取失败: {e}")
    
def split_text(text: str, chunk_size: int = 200, chunk_overlap: int = 40,) -> list[str]:
    """
    将文本按照 chunk_size 分割成多个块
    每两段文本之间有 chunk_overlap 重叠
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if chunk_overlap < 0:
        # return f"切片失败: chunk_overlap 必须大于等于0"
        raise ValueError("切片失败: chunk_overlap 必须大于等于0")
    if chunk_overlap >= chunk_size:
        # return f"切片失败: chunk_overlap 必须小于 chunk_size"
        raise ValueError("切片失败: chunk_overlap 必须小于 chunk_size")
    
    chunks = []
    if len(text) <= chunk_size:
        return [text]

    for i in range(0, len(text), chunk_size - chunk_overlap):
        chunks.append(text[i: i + chunk_size])
        if i + chunk_size >= len(text): # 避免产生冗余的尾部 chunk
            break
    return chunks

if __name__ == "__main__":
    # 测试代码
    # path = os.path.join(WORKSPACE_ROOT, "month02_rag_agent/docs/agent_infra.md")
    # result = load_text_file(path)
    # print(f"加载结果: {result}")

    # if isinstance(result, dict):
    #     chunks = split_text(result["text"])
    #     for i, chunk in enumerate(chunks):
    #         print(f"chunk_{i}: \n{chunk}\n")
    for length in [1, 200, 201, 360, 500]:
        chunks = split_text("a" * length)
        print(length, [len(chunk) for chunk in chunks])