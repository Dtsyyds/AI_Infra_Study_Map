import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.contracts.task import TaskRequest


def test_budget_is_strict_and_bounded():
    for value in [True, "1000", 999, 1800001, 1000.1]:
        with pytest.raises(ValidationError):
            TaskRequest(document_id="DOC-001", session_id="s", budget_ms=value)


def test_ast_dependency_direction():
    for path in Path("core").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            assert all(m.split(".")[0] not in {"api", "runtime"} for m in modules), path
