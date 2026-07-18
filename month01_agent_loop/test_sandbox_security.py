"""
test_sandbox_security.py

测试文件沙盒安全性, 主要是为了测试 read_file 和 write_file 这两个工具函数的安全性。


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
    assert "outside content" not in result

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

def test_read_file_allows_normal_workspace_file(tmp_path, monkeypatch):
    # 创建临时 workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # 创建 workspace 内部文件
    inside_file = workspace / "safe.txt"
    inside_file.write_text("inside content")

    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace),
    )

    # 模拟程序从 workspace 外启动
    # monkeypatch.chdir(inside_file)  # i是一个文件，不能作为当前目录

    # 测试 read_file 是否可以正常读取 workspace 内文件
    result = tools.read_file("safe.txt")

    assert result.startswith("TOOL_OK:")
    
    assert "inside content" in result

def test_read_file_allows_symlinked_workspace_root(tmp_path, monkeypatch):
    # 创建临时 workspace
    real_workspace = tmp_path / "real_workspace"
    real_workspace.mkdir()

    # 创建内部文件
    inside_file = real_workspace / "safe.txt"
    inside_file.write_text("inside content")

    workspace_link = tmp_path / "workspace_link"

    workspace_link.symlink_to(
        real_workspace,
        target_is_directory=True,
    )

    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace_link),
    )

    # 模拟程序从 workspace 外启动

    result = tools.read_file("safe.txt")    # 直接读不需要再额外创建文件链接

    # print("lexical root:", tools.WORKSPACE_ROOT)
    # print(
    #     "canonical root:",
    #     os.path.realpath(tools.WORKSPACE_ROOT),
    # )
    # print(
    #     "canonical candidate:",
    #     os.path.realpath(
    #         os.path.join(tools.WORKSPACE_ROOT, "safe.txt")
    #     ),
    # )

    assert result.startswith("TOOL_OK:")
    assert "inside content" in result

def test_write_file_rejects_symlink_escape(tmp_path, monkeypatch):
    # 创建临时 workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # 创建 workspace 外部文件
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content")

    # 在 workspace 内部创建指向外部文件的符号链接
    link_path = workspace / "link.txt"
    link_path.symlink_to(outside_file)  # 指向外面的 outside.txt 文件

    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace),
    )

    result = tools.write_file(str(link_path), "attacked")

    assert result.startswith("TOOL_ERROR:")
    assert outside_file.read_text(
        encoding="utf-8"
    ) == "outside content"
    # 安全测试不能只检查错误信息，还必须检测被保护资源没有修改

def test_list_files_never_exposes_sensitive_file(tmp_path, monkeypatch):
    # 创建临时 workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # 在 workspace 内部创建文件
    inside_file = workspace / "visible.txt"
    env_file = workspace / ".env"
    hidden_file = workspace / ".notes"
    hidden_file.write_text(
        "normal hidden file",
        encoding="utf-8",
    )

    inside_file.write_text("inside content")
    env_file.write_text("sensitive data")

    monkeypatch.setattr(
        tools,
        "WORKSPACE_ROOT",
        str(workspace),
    )

    result = tools.list_files(".", show_hidden="true")

    assert result.startswith("TOOL_OK:")
    assert "visible.txt" in result
    assert ".env" not in result
    assert ".notes" in result
    assert ".env" not in result


