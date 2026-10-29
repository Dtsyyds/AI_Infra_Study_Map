"""Read-only archive verification. Integrity PASS is never project acceptance."""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


def verify(directory):
    archive = directory / "acceptance_bundle.tar.gz"
    checksum = (directory / "acceptance_bundle.sha256").read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise ValueError("ARCHIVE_HASH_MISMATCH")
    prefix = directory.name + "/"
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)):
            raise ValueError("DUPLICATE_ARCHIVE_MEMBER")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != member.name
                or not member.name.startswith(prefix)
            ):
                raise ValueError("UNSAFE_ARCHIVE_MEMBER")
            if {".env", ".git", ".pgdata", ".venv", ".postgres", ".python"} & set(path.parts):
                raise ValueError("EXCLUDED_ENVIRONMENT_INCLUDED")

        def content(name):
            if prefix + name not in names:
                raise ValueError("MISSING_ARCHIVE_FILE: " + name)
            stream = tar.extractfile(prefix + name)
            if stream is None:
                raise ValueError("MISSING_ARCHIVE_FILE")
            return stream.read()

        manifest = json.loads(content("evidence_manifest.json"))
        if manifest["run_id"] != directory.name:
            raise ValueError("RUN_ID_MISMATCH")
        expected_names = {prefix + name for name in manifest["files_sha256"]}
        if set(names) != expected_names | {prefix + "evidence_manifest.json"}:
            raise ValueError("EVIDENCE_INVENTORY_MISMATCH")
        for name, expected in manifest["files_sha256"].items():
            if hashlib.sha256(content(name)).hexdigest() != expected:
                raise ValueError("EVIDENCE_HASH_MISMATCH: " + name)
        tests = json.loads(content("tests/index.json"))
        suite = ElementTree.fromstring(content(tests["final_junit"]))
        counts = {
            key: sum(int(s.attrib.get(key, 0)) for s in suite.findall("testsuite"))
            for key in ("tests", "errors", "failures", "skipped")
        }
        counts["passed"] = (
            counts["tests"] - counts["errors"] - counts["failures"] - counts["skipped"]
        )
        cases = suite.findall("testsuite/testcase")
        actual = {"tests": len(cases), "errors": 0, "failures": 0, "skipped": 0}
        for case in cases:
            outcomes = [tag for tag in ("error", "failure", "skipped") if case.find(tag) is not None]
            if len(outcomes) > 1:
                raise ValueError("CONTRADICTORY_TEST_OUTCOME")
            if outcomes:
                actual[{"error": "errors", "failure": "failures", "skipped": "skipped"}[outcomes[0]]] += 1
        if any(counts[key] != value for key, value in actual.items()):
            raise ValueError("JUNIT_CASE_COUNT_MISMATCH")
        if counts != tests["counts"]:
            raise ValueError("TEST_TOTAL_MISMATCH")
        source = json.loads(content("source_manifest.json"))
        if {name for name in names if name.startswith(prefix + "source/")} != {
            prefix + "source/" + name for name in source["files_sha256"]
        }:
            raise ValueError("SOURCE_INVENTORY_MISMATCH")
        for path, expected in source["files_sha256"].items():
            if hashlib.sha256(content("source/" + path)).hexdigest() != expected:
                raise ValueError("SOURCE_HASH_MISMATCH")
        match = json.loads(content("tests/source_match.json"))
        if match["changed_since_test"]:
            raise ValueError("SOURCE_CHANGED_SINCE_TEST")
        snapshot = json.loads(content(match["tested_source_snapshot"]))
        protected = {
            name for name in source["files_sha256"]
            if name.startswith(("core/", "runtime/", "api/", "config/", "tests/", "deploy/", "examples/"))
            and name not in {"examples/package_delivery.py", "examples/verify_delivery.py"}
        }
        if any(source["files_sha256"][name] != snapshot.get(name) for name in protected):
            raise ValueError("SOURCE_CHANGED_SINCE_TEST")
        if match["runtime_and_test_files_compared"] != len(protected):
            raise ValueError("SOURCE_COMPARISON_COUNT_MISMATCH")
        commands = [json.loads(line) for line in content("commands.jsonl").splitlines()]
        regression = next((row for row in reversed(commands)
                           if tests["final_junit"] in " ".join(row["command"])), None)
        if (regression is None or regression["commit"] != match["tested_commit"]
                or regression["source_manifest"] != match["tested_source_snapshot"]):
            raise ValueError("TESTED_SOURCE_COMMAND_MISMATCH")
        runs = json.loads(content("runs/index.json"))
        if runs["missing"]:
            raise ValueError("REFERENCED_LOG_MISSING")
        for name in runs["referenced_process_logs"]:
            if prefix + name not in expected_names:
                raise ValueError("REFERENCED_LOG_MISSING")
        for name in runs["directories"]:
            if not any(member.startswith(prefix + name + "/") for member in expected_names):
                raise ValueError("REFERENCED_RUN_MISSING")
        gates = json.loads(content("gates.json"))
        if gates["overall"] == "PASS" and (
            gates["p0"] != "PASS" or counts["skipped"] or counts["errors"] or counts["failures"]
        ):
            raise ValueError("FALSE_OVERALL_PASS")
    return {
        "integrity_status": "PASS",
        "scope": "hashes, counts, references and source consistency only",
        "source_commit": source["commit"],
        "archive_sha256": checksum,
        "verified_files": len(manifest["files_sha256"]),
        "test_counts": counts,
        "project_status": gates["overall"],
        "external_acceptance": gates["external_acceptance"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    (args.directory / "bundle_validation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
