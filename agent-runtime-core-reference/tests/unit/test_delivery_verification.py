import hashlib
import io
import json
import tarfile

import pytest

from examples.verify_delivery import verify


def bundle(tmp_path, defect=None):
    directory = tmp_path / "run"
    directory.mkdir()
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    source = b"tested platform source"
    code_hash = sha(source)
    total = 2 if defect == "junit" else 1
    files = {
        "source/runtime/module.py": source,
        "reports/run/tests.xml": (
            f'<testsuites><testsuite tests="{total}" failures="0" errors="0" skipped="0">'
            '<testcase name="actual_test"/></testsuite></testsuites>'
        ).encode(),
    }
    data = {
        "source_manifest.json": {"commit": "docs-commit", "files_sha256": {"runtime/module.py": code_hash}},
        "tests/index.json": {"final_junit": "reports/run/tests.xml", "counts": {
            "tests": total, "passed": total, "errors": 0, "failures": 0, "skipped": 0}},
        "tests/source_match.json": {
            "changed_since_test": [], "tested_source_snapshot": "reports/run/source.json",
            "tested_commit": "test-commit", "runtime_and_test_files_compared": 1},
        "reports/run/source.json": {"runtime/module.py": "0" * 64 if defect == "source" else code_hash},
        "runs/index.json": {"missing": [], "directories": [], "referenced_process_logs":
                            ["reports/process-missing.log"] if defect == "log" else []},
        "gates.json": {"overall": "PARTIAL", "p0": "PASS", "external_acceptance": "NOT_PERFORMED"},
    }
    files.update({name: json.dumps(value).encode() for name, value in data.items()})
    files["commands.jsonl"] = json.dumps({
        "command": ["pytest", "--junitxml=reports/run/tests.xml"],
        "commit": "wrong" if defect == "command" else "test-commit",
        "source_manifest": "reports/run/source.json",
    }).encode()
    if defect == "source_inventory":
        files["source/runtime/unlisted.py"] = b"unlisted source"
    inventory = {name: sha(value) for name, value in files.items()}
    if defect == "inventory":
        files["unlisted.log"] = b"unlisted evidence"
    files["evidence_manifest.json"] = json.dumps({
        "run_id": "wrong" if defect == "run" else "run", "files_sha256": inventory,
    }).encode()
    archive = directory / "acceptance_bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, value in files.items():
            info = tarfile.TarInfo("run/" + name)
            info.size = len(value)
            tar.addfile(info, io.BytesIO(value))
    (directory / "acceptance_bundle.sha256").write_text(sha(archive.read_bytes()))
    return directory


def test_complete_archive_passes_consistency_only(tmp_path):
    result = verify(bundle(tmp_path))
    assert result["integrity_status"] == "PASS" and result["project_status"] == "PARTIAL"


@pytest.mark.parametrize("defect,error", [
    ("inventory", "EVIDENCE_INVENTORY_MISMATCH"),
    ("run", "RUN_ID_MISMATCH"),
    ("source_inventory", "SOURCE_INVENTORY_MISMATCH"),
    ("junit", "JUNIT_CASE_COUNT_MISMATCH"),
    ("source", "SOURCE_CHANGED_SINCE_TEST"),
    ("log", "REFERENCED_LOG_MISSING"),
    ("command", "TESTED_SOURCE_COMMAND_MISMATCH"),
])
def test_self_consistent_hashes_do_not_hide_missing_evidence(tmp_path, defect, error):
    # All checksums are deliberately recomputed; these tests exercise semantic
    # consistency rather than relying on an accidental corrupt archive hash.
    with pytest.raises(ValueError, match=error):
        verify(bundle(tmp_path, defect))
