import hashlib
import json
from pathlib import Path
from typing import Any

from core.contracts.repair import CaseManifest, safe_path
from runtime.tools.docker_backend import IMAGE

ROOT = Path(__file__).resolve().parents[2]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_hash(files: dict[str, str]) -> str:
    return sha(canonical(files))


def runner_source() -> str:
    return (ROOT / "deploy/repair_runner.py").read_text()


def approved_argv() -> list[str]:
    return ["python", "-I", "-B", "-c", runner_source()]


def validate_policy(manifest: CaseManifest) -> None:
    if manifest.image_digest != IMAGE:
        raise ValueError("UNAPPROVED_IMAGE")
    if manifest.test_argv != approved_argv() or manifest.runner_hash != sha(
        runner_source().encode()
    ):
        raise ValueError("UNAPPROVED_RUNNER")
    required = {
        "tests",
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "requirements.lock",
        "runtime",
        "core",
        "deploy",
    }
    if not required.issubset(manifest.forbidden_paths):
        raise ValueError("MISSING_PROTECTED_PATHS")


def load_case(case_id: str) -> tuple[CaseManifest, dict[str, str]]:
    import re

    if not re.fullmatch(r"case-[0-9]{2}", case_id):
        raise ValueError("INVALID_CASE_ID")
    root = ROOT / "examples/repair_cases" / case_id
    if root.is_symlink() or (root / "repo").is_symlink():
        raise ValueError("SYMLINK_CASE")
    manifest = CaseManifest.model_validate_json((root / "manifest.json").read_bytes())
    validate_policy(manifest)
    files = {}
    for p in sorted((root / "repo").rglob("*")):
        if p.is_symlink():
            raise ValueError("SYMLINK_FILE")
        if p.is_file():
            key = safe_path(p.relative_to(root / "repo").as_posix())
            files[key] = p.read_text()
    if tree_hash(files) != manifest.repo_content_hash:
        raise ValueError("BASE_HASH_MISMATCH")
    if (
        tree_hash({p: v for p, v in files.items() if p not in manifest.allowed_paths})
        != manifest.test_suite_hash
    ):
        raise ValueError("TEST_HASH_MISMATCH")
    return manifest, files
