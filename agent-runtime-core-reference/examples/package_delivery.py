"""Create an immutable review bundle from recorded evidence; never promotes a gate to PASS."""

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main():
    state = json.loads((ROOT / "TASK_STATUS.json").read_text())
    run = state["run_id"]
    report = ROOT / "reports" / run
    final_junit = state.get("final_test_results", "final-test-results.xml")
    output = ROOT / "delivery" / run
    if output.exists():
        raise RuntimeError("delivery exists; never overwrite a published local review snapshot")
    if git("status", "--porcelain"):
        raise RuntimeError("commit authorized source changes before bundling")
    if state["overall"] != "PARTIAL":
        raise RuntimeError("this partial evidence packager cannot claim acceptance")
    required = [
        "FINAL_REPORT.md",
        "environment.json",
        "commands.jsonl",
        final_junit,
        "resource-cleanup.json",
    ]
    for name in required:
        if not (report / name).is_file():
            raise RuntimeError("missing required evidence: " + name)
    output.mkdir(parents=True)
    source_paths = git("ls-files").splitlines()
    hashes = {}
    for path in source_paths:
        source = ROOT / path
        if (
            source.is_symlink()
            or path == ".env"
            or any(part in {".pgdata", ".git", ".venv"} for part in Path(path).parts)
        ):
            raise RuntimeError("unsafe source asset: " + path)
        hashes[path] = sha(source)
        copy(source, output / "source" / path)
    write(
        output / "source_manifest.json",
        {
            "commit": git("rev-parse", "HEAD"),
            "dirty": False,
            "baseline_commit": git("rev-list", "--max-parents=0", "HEAD"),
            "files_sha256": hashes,
            "formal_learning_project_accessed_or_modified": False,
            "month01_month04_source_copied": False,
            "scope": str(ROOT),
            "git_history": git("log", "--format=%H %s").splitlines(),
            "note": "hashes establish consistency, not authenticity; no .git or author contact exported",
        },
    )
    commands = [json.loads(line) for line in (report / "commands.jsonl").read_text().splitlines()]
    regression = next(
        row for row in reversed(commands) if final_junit in " ".join(row["command"])
    )
    tested = json.loads((ROOT / regression["source_manifest"]).read_text())
    protected = {
        p
        for p in hashes
        if p.startswith(("core/", "runtime/", "api/", "config/", "tests/", "deploy/", "examples/"))
        and p not in {"examples/package_delivery.py", "examples/verify_delivery.py"}
    }
    changed_since_test = sorted(p for p in protected if hashes[p] != tested.get(p))
    if changed_since_test:
        raise RuntimeError(
            "runtime/test files changed since final regression: " + str(changed_since_test)
        )
    write(
        output / "tests/source_match.json",
        {
            "tested_commit": regression["commit"],
            "tested_source_snapshot": regression["source_manifest"],
            "runtime_and_test_files_compared": len(protected),
            "changed_since_test": changed_since_test,
        },
    )
    shutil.copytree(report, output / "reports" / run)
    evidence_roots = [report]
    for supplementary in state.get("supplementary_run_ids", []):
        directory = ROOT / "reports" / supplementary
        shutil.copytree(directory, output / "reports" / supplementary)
        evidence_roots.append(directory)
    # Preserve only process logs explicitly referenced by this run's evidence.
    referenced = set()
    for directory in evidence_roots:
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".log"}:
                referenced.update(re.findall(r"reports/process-[0-9a-f-]+\.log", path.read_text()))
    missing = []
    for name in sorted(referenced):
        if (ROOT / name).is_file():
            copy(ROOT / name, output / name)
        else:
            missing.append(name)
    for name in ("acceptance.md", "test-results.xml", "demo-results.json"):
        historical = ROOT / "reports/20261022-reference" / name
        if historical.is_file():
            copy(historical, output / "history/20261022-reference" / name)
    previous = ROOT / "reports/20261024T135237Z-enhancement-p0/p0_revalidation.md"
    if previous.is_file():
        copy(previous, output / "history/20261024T135237Z-enhancement-p0/p0_revalidation.md")
    for name in ("FINAL_REPORT.md", "environment.json", "commands.jsonl"):
        copy(report / name, output / name)
    for name in ("blockers.md", "resume.md", "REPRODUCE.md"):
        copy(ROOT / name, output / name)
    for name in ("job_requirements_matrix.md", "resume_evidence.md", "interview_pack.md"):
        copy(ROOT / "docs" / name, output / name)
    write(output / "gates.json", state)
    write(
        output / "workspace_changes.json",
        {
            "baseline_commit": git("rev-list", "--max-parents=0", "HEAD"),
            "current_commit": git("rev-parse", "HEAD"),
            "tracked_worktree_status": git("status", "--porcelain"),
            "changes_since_baseline": git(
                "diff", "--name-status", git("rev-list", "--max-parents=0", "HEAD"), "HEAD"
            ).splitlines(),
            "generated_ignored_paths": ["reports/" + run, "delivery/" + run],
            "preserved_database": ".pgdata",
            "formal_project_modified": False,
            "resources": json.loads((report / "resource-cleanup.json").read_text()),
        },
    )
    suites = ElementTree.parse(report / final_junit).getroot().findall("testsuite")
    counts = {
        key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "errors", "failures", "skipped")
    }
    counts["passed"] = counts["tests"] - counts["errors"] - counts["failures"] - counts["skipped"]
    skipped = [
        {
            "class": case.attrib.get("classname"),
            "name": case.attrib.get("name"),
            "reason": node.attrib.get("message"),
        }
        for suite in suites
        for case in suite.findall("testcase")
        if (node := case.find("skipped")) is not None
    ]
    write(
        output / "tests/index.json",
        {
            "final_junit": "reports/" + run + "/" + final_junit,
            "counts": counts,
            "skips": skipped,
            "raw_logs": [
                str(path.relative_to(output)) for path in (output / "reports" / run).glob("*.log")
            ],
            "historical_failed_commands_retained": True,
            "warning": "unit/protocol/PG/process/container scopes differ; test counts alone do not close project gates",
        },
    )
    for path in report.glob("*.xml"):
        copy(path, output / "tests" / path.name)
    run_paths = [
        "p0",
        "repair-demo",
        "repair-process",
        "reliability",
        "additional-faults",
        "cli-probe",
        "harness-acceptance",
        "sandbox-matrix",
        "repair-recovery",
    ]
    write(
        output / "runs/index.json",
        {
            "directories": ["reports/" + run + "/" + p for p in run_paths if (report / p).is_dir()],
            "referenced_process_logs": sorted(referenced),
            "missing": missing,
        },
    )
    demo = json.loads((report / "repair-demo/result.json").read_text())
    artifacts = []
    for item in demo["artifacts"]:
        source = ROOT / item["file"]
        assert sha(source) == item["sha256"] and source.stat().st_size == item["bytes"]
        target = output / "artifacts" / source.name
        copy(source, target)
        artifacts.append(
            {
                **item,
                "bundle_file": str(target.relative_to(output)),
                "task_id": demo["task_id"],
                "scope": "Fake-planner repair evidence; not real-model output",
            }
        )
    fixture = report / "repair-process/patch-storage-fixture.json"
    fixture_meta = json.loads((report / "repair-process/result.json").read_text())
    assert sha(fixture) == fixture_meta["artifact_sha256"]
    copy(fixture, output / "artifacts" / fixture.name)
    documents = []
    p0 = json.loads((report / "p0/results.json").read_text())
    for task in p0.get("identities", []):
        for item in task["artifacts"]:
            source = report / "p0" / item["file"]
            assert sha(source) == item["sha256"] and source.stat().st_size == item["bytes"]
            target = output / "artifacts" / source.name
            copy(source, target)
            documents.append({
                **item,
                "task_id": task["task_id"],
                "bundle_file": str(target.relative_to(output)),
                "scope": "deterministic document_review final report",
            })
    write(
        output / "artifacts/index.json",
        {
            "delivered_patch_count": int(demo["business_outcome"] == "DELIVERED"),
            "real_model_delivered_patch_count": 0,
            "document_final_reports": documents,
            "p0_status": p0["overall"],
            "repair_artifacts": artifacts,
            "repair_business_outcome": demo["business_outcome"],
            "synthetic_patch_storage_fixture": {
                "file": fixture.name,
                "sha256": sha(fixture),
                "task_id": fixture_meta["task_id"],
                "candidate_no": 1,
                "scope": "storage/recovery only; neither a verified candidate nor model output",
            },
        },
    )
    shutil.copytree(report / "benchmarks", output / "benchmarks")
    load_evidence = report / state.get("load_evidence_dir", "benchmarks")
    for name in ("reliability", "additional-faults"):
        for file in ("raw.jsonl", "summary.json"):
            copy(report / name / file, output / "benchmarks" / name / file)
    write(
        output / "benchmarks/index.json",
        {
            "real_model_tasks": len(
                (report / "benchmarks/model_raw.jsonl").read_text().splitlines()
            )
            if (report / "benchmarks/model_raw.jsonl").exists()
            else 0,
            "planned_real_model_tasks": 72,
            "deterministic_load_submissions": len(
                (load_evidence / "load_raw.jsonl").read_text().splitlines()
            )
            if (load_evidence / "load_raw.jsonl").exists()
            else 0,
            "deterministic_load_evidence": str(load_evidence.relative_to(report)),
            "planned_deterministic_load_submissions": 270,
            "deterministic_fault_records": len(
                (report / "reliability/raw.jsonl").read_text().splitlines()
            ),
            "additional_fault_records": len(
                (report / "additional-faults/raw.jsonl").read_text().splitlines()
            ),
            "status": "INCOMPLETE_BLOCKED",
            "statistics_program": "source/examples/summarize.py",
            "scopes": "fault probes are separate from load and real-model effects; no pooled success rate",
        },
    )
    if state.get("previous_run_id"):
        previous_dir = ROOT / "delivery" / state["previous_run_id"]
        if previous_dir.is_dir():
            for filename in (
                "FINAL_REPORT.md",
                "source_manifest.json",
                "acceptance_bundle.sha256",
                "gates.json",
            ):
                copy(
                    previous_dir / filename,
                    output / "history" / state["previous_run_id"] / filename,
                )
    # Include every export except the inventory itself and archive, avoiding circular hashes.
    write(
        output / "evidence_manifest.json",
        {
            "run_id": run,
            "files_sha256": {
                str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()
            },
        },
    )
    archive = output / "acceptance_bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.rglob("*")):
            if path.is_file() and path != archive:
                tar.add(path, arcname=str(Path(run) / path.relative_to(output)), recursive=False)
    checksum = sha(archive)
    (output / "acceptance_bundle.sha256").write_text(checksum + "  acceptance_bundle.tar.gz\n")
    write(
        output / "packaging.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "archive_sha256": checksum,
            "source_commit": git("rev-parse", "HEAD"),
            "missing_referenced_logs": missing,
            "external_acceptance": "NOT_PERFORMED",
            "note": "packaging metadata and archive checksum are outside archive to avoid circularity",
        },
    )
    print(
        json.dumps(
            {
                "bundle": str(archive),
                "sha256": checksum,
                "test_counts": counts,
                "missing_referenced_logs": missing,
                "overall": "PARTIAL",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
