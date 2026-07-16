"""
test_sandbox_security.py

测试文件沙盒安全性
"""

"""
代码中出现了 tmp_path 和 monkeypatch，这是 pytest 提供的强大功能：

tmp_path：pytest 内置夹具，会在每次测试运行时自动创建一个临时目录，测试结束后自动删除。这保证了测试的隔离性，不会弄脏你的真实文件系统。

monkeypatch：用于在测试运行时动态修改对象属性、环境变量或当前工作目录。测试结束后会自动恢复原状，不影响其他测试。这对于测试文件路径、环境变量等敏感配置非常有用。

test_read_file_rejects_symlink_escape:防止“路径遍历攻击”——禁止通过软链接读取工作区之外的文件。


"""
import tools

def test_read_file_rejects_symlink_escape(tmp_path, monkeypatch):
    # 创建临时 workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # 创建 workspace 外部文件
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content")

    # 在 workspace 内部创建指向外部文件的符号链接
    link_path = workspace / "link.txt"
    link_path.symlink_to(outside_file)  # 指向外面的 outside.txt 文件

    # 测试 read_file 是否拒绝符号链接逃逸
    # 测试期间临时替换项目 workspace
    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace),
    )
    # 通过 monkeypatch.setattr，强制将 tools.WORKSPACE_ROOT 设置为 workspace 的路径（模拟程序认定此目录为安全边界）
    # Act
    result = tools.read_file(str(link_path))

    # Assert：不能通过符号链接读取外部内容
    assert result.startswith("TOOL_ERROR:")
    assert "outside secret" not in result

def test_write_file_uses_validated_workspace_path(tmp_path, monkeypatch):
    # Arrange
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside_cwd = tmp_path / "outside_cwd"
    outside_cwd.mkdir()

    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace),
    )

    # 模拟程序从 workspace 外启动
    monkeypatch.chdir(outside_cwd)

    # Act
    result = tools.write_file(
        "created.txt",
        "hello sandbox",
    )

    expected_file = workspace / "created.txt"
    wrong_file = outside_cwd / "created.txt"

    # Assert
    assert result.startswith("TOOL_OK:")
    assert expected_file.exists()
    assert expected_file.read_text(
        encoding="utf-8"
    ) == "hello sandbox"

    # 原始相对路径不能写到当前工作目录
    assert not wrong_file.exists()
