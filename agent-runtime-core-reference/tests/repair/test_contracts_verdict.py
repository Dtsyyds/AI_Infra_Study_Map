import copy
import json
from uuid import uuid4

import pytest

from core.contracts.repair import CaseManifest, Patch, RunRecord, VerificationIdentity
from runtime.repair.manifest import canonical, load_case, sha, tree_hash, validate_policy
from runtime.repair.verdict import judge
from runtime.repair.workspace import apply_patch


def evidence():
    manifest, files = load_case("case-01")
    identity = VerificationIdentity(
        task_id=str(uuid4()),
        candidate_no=1,
        candidate_hash=tree_hash(files),
        manifest_hash=sha(canonical(manifest.model_dump())),
        verification_run_id=str(uuid4()),
        nonce=uuid4().hex,
        image_digest=manifest.image_digest,
        test_suite_hash=manifest.test_suite_hash,
        repo_content_hash=manifest.repo_content_hash,
        runner_hash=manifest.runner_hash,
    )
    report = {
        "schema_version": "1",
        "identity": identity.model_dump(),
        "collected_ids": manifest.expected_test_ids,
        "tests": [{"test_id": i, "outcome": "passed"} for i in manifest.expected_test_ids],
        "protected_hash": manifest.test_suite_hash,
        "complete": True,
    }
    record = RunRecord(identity=identity, started=True, exit_code=0, cleanup_confirmed=True)
    return manifest, record, report


def evaluate(m, r, report):
    raw = canonical(report)
    r.report_sha256 = sha(raw)
    return judge(m, r, raw)


def test_valid_and_failed_records_are_distinct():
    m, r, report = evidence()
    assert evaluate(m, r, report).status == "PASS"
    report["tests"][0]["outcome"] = "failed"
    r.exit_code = 1
    assert evaluate(m, r, report).status == "FAIL"
    r.exit_code = 0
    assert evaluate(m, r, report).reason_code == "EXIT_REPORT_CONTRADICTION"


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "skip",
        "xfail",
        "xpass",
        "missing_id",
        "duplicate_id",
        "not_complete",
        "protected",
        "other_task",
        "old_candidate",
        "nonce",
        "verification",
        "suite",
        "image",
        "extra",
    ],
)
def test_false_pass_matrix(mutation):
    m, r, report = evidence()
    if mutation == "empty":
        report["tests"], report["collected_ids"] = [], []
    elif mutation in {"skip", "xfail", "xpass"}:
        report["tests"][0]["outcome"] = {"skip": "skipped"}.get(mutation, mutation)
    elif mutation == "missing_id":
        report["tests"].pop()
    elif mutation == "duplicate_id":
        report["tests"].append(report["tests"][0])
    elif mutation == "not_complete":
        report["complete"] = False
    elif mutation == "protected":
        report["protected_hash"] = "0" * 64
    elif mutation == "extra":
        report["model_says_pass"] = True
    else:
        key = {
            "other_task": "task_id",
            "old_candidate": "candidate_hash",
            "verification": "verification_run_id",
            "suite": "test_suite_hash",
            "image": "image_digest",
        }.get(mutation, mutation)
        report["identity"][key] = "0" * 64
    assert evaluate(m, r, report).status != "PASS"


@pytest.mark.parametrize(
    "mutation",
    [
        "not_started",
        "timeout",
        "resource",
        "cleanup",
        "missing",
        "corrupt",
        "hash",
        "trusted_mismatch",
    ],
)
def test_trusted_run_and_report_errors(mutation):
    m, r, report = evidence()
    raw = canonical(report)
    r.report_sha256 = sha(raw)
    if mutation == "not_started":
        r.started = False
    if mutation == "timeout":
        r.timed_out = True
    if mutation == "resource":
        r.resource_error = True
    if mutation == "cleanup":
        r.cleanup_confirmed = False
    if mutation == "missing":
        raw = None
    if mutation == "corrupt":
        raw = b'{"complete":'
        r.report_sha256 = sha(raw)
    if mutation == "hash":
        r.report_sha256 = "0" * 64
    if mutation == "trusted_mismatch":
        r.identity.manifest_hash = "0" * 64
    assert judge(m, r, raw).status != "PASS"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "2"),
        ("expected_test_ids", []),
        ("expected_test_ids", ["x", "x"]),
        ("allowed_paths", ["../src/a.py"]),
        ("allowed_paths", ["/tmp/x.py"]),
        ("allowed_paths", ["src/conftest.py"]),
        ("allowed_paths", ["tests/test.py"]),
        ("snapshot_hash", "0" * 64),
        ("limits", {"candidates": 4}),
        ("limits", {"candidates": True}),
        ("limits", {"files": 4}),
        ("limits", {"wall_seconds": 31}),
        ("shell", "whoami"),
    ],
)
def test_manifest_rejects_invalid_data(field, value):
    manifest, _ = load_case("case-01")
    payload = manifest.model_dump()
    payload[field] = value
    with pytest.raises(ValueError):
        CaseManifest.model_validate(payload)


def test_manifest_approved_command_image_and_missing_fields():
    manifest, _ = load_case("case-01")
    for field in ["repo_content_hash", "test_suite_hash", "expected_test_ids", "image_digest"]:
        value = manifest.model_dump()
        del value[field]
        with pytest.raises(ValueError):
            CaseManifest.model_validate(value)
    for field, value in [
        ("test_argv", ["sh", "-c", "true"]),
        ("image_digest", manifest.image_digest[:-1] + "0"),
    ]:
        bad = copy.deepcopy(manifest)
        setattr(bad, field, value)
        with pytest.raises(ValueError):
            validate_policy(bad)


def test_patches_rebuild_and_preserve_suite():
    m, base = load_case("case-01")
    patch = Patch.model_validate(
        json.loads(open("tests/repair/private_reference_patches.json").read())["case-01"]
    )
    fixed = apply_patch(m, base, patch)
    assert fixed != base
    assert fixed == apply_patch(m, base, patch)
    assert fixed["tests/test_case.py"] == base["tests/test_case.py"]
    assert base["src/solution.py"] != fixed["src/solution.py"]


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_case.py",
        "conftest.py",
        "pytest.ini",
        "requirements.lock",
        "runtime/x.py",
        "/tmp/x",
        "../src/solution.py",
        "src/../solution.py",
    ],
)
def test_patch_paths_rejected(path):
    m, base = load_case("case-01")
    with pytest.raises(ValueError):
        apply_patch(
            m, base, Patch.model_validate({"edits": [{"path": path, "old": "x", "new": "y"}]})
        )


@pytest.mark.parametrize("new", ["x" * 33000, "\x00", "\n".join("line" for _ in range(201))])
def test_patch_resource_bounds(new):
    m, base = load_case("case-01")
    with pytest.raises(ValueError):
        apply_patch(
            m,
            base,
            Patch.model_validate(
                {"edits": [{"path": "src/solution.py", "old": "max(low, x)", "new": new}]}
            ),
        )


def test_all_cases_load_without_executing_sources():
    for index in range(1, 13):
        m, files = load_case(f"case-{index:02}")
        assert tree_hash(files) == m.repo_content_hash


def test_duplicate_json_keys_cannot_hide_incomplete_report():
    m, r, report = evidence()
    raw = canonical(report).replace(b'"complete":true', b'"complete":false,"complete":true')
    r.report_sha256 = sha(raw)
    assert judge(m, r, raw).status == "INCOMPLETE"


def test_model_evaluation_requires_current_full_harness_qualification(tmp_path):
    from examples.harness_acceptance import qualified

    assert not qualified(tmp_path)
    (tmp_path / "summary.json").write_text('{"status":"PASS"}')
    (tmp_path / "results.jsonl").write_text('')
    assert not qualified(tmp_path)


def test_harness_qualification_with_null_report_stays_unqualified(tmp_path):
    from examples.harness_acceptance import qualified

    manifest, record, _ = evidence()
    (tmp_path / "summary.json").write_text('{"status":"PASS"}')
    (tmp_path / "results.jsonl").write_text(json.dumps({"case_id": manifest.case_id,
        "mode": "correct", "result": {"record": record.model_dump(), "raw_report": None}}) + "\n")
    assert qualified(tmp_path) is False
