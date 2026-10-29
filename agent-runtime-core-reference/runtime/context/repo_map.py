"""Syntax-only Python index. No imports or execution of repository code."""

import ast
from typing import Any

from core.contracts.repair import safe_path
from runtime.repair.manifest import sha


def repo_map(files: dict[str, str], allowed: list[str]) -> list[dict]:
    result = []
    for path in sorted(allowed):
        safe_path(path)
        if path not in files or not path.endswith(".py"):
            continue
        source = files[path]
        item: dict[str, Any] = {
            "path": path,
            "content_hash": sha(source.encode()),
            "symbols": [],
            "imports": [],
            "references": [],
        }
        try:
            tree = ast.parse(source)
        except SyntaxError:
            item["unresolved"] = "SYNTAX_ERROR"
            result.append(item)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                item["symbols"].append(
                    {
                        "name": node.name,
                        "kind": type(node).__name__,
                        "line": node.lineno,
                        "end_line": node.end_lineno,
                        "signature": ast.unparse(node.args)
                        if not isinstance(node, ast.ClassDef)
                        else None,
                    }
                )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                item["imports"].append({"line": node.lineno, "syntax": ast.unparse(node)})
            if isinstance(node, ast.Call):
                item["references"].append(
                    {
                        "line": node.lineno,
                        "syntax": ast.unparse(node.func),
                        "resolution": "DIRECT_SYNTAX"
                        if isinstance(node.func, ast.Name)
                        else "UNRESOLVED",
                    }
                )
        result.append(item)
    return result
