"""Independent verdict; no planner imports, no trust in a model's success claim."""

import json

from pydantic import ValidationError

from core.contracts.repair import CaseManifest, RunnerReport, RunRecord, Verdict
from runtime.repair.manifest import canonical, sha


def judge(manifest: CaseManifest, record: RunRecord, raw: bytes | None) -> Verdict:
    def verdict(status, reason, failures=None):
        return Verdict(
            status=status, reason_code=reason, identity=record.identity, failed_ids=failures or []
        )

    identity = record.identity
    if (
        identity.manifest_hash != sha(canonical(manifest.model_dump()))
        or identity.image_digest != manifest.image_digest
        or identity.test_suite_hash != manifest.test_suite_hash
        or identity.repo_content_hash != manifest.repo_content_hash
        or identity.runner_hash != manifest.runner_hash
    ):
        return verdict("ERROR", "TRUSTED_IDENTITY_MISMATCH")
    if not record.started:
        return verdict("NOT_RUN", "RUNNER_NOT_STARTED")
    if record.timed_out or record.resource_error:
        return verdict("ERROR", "RESOURCE_OR_TIMEOUT")
    if not record.cleanup_confirmed:
        return verdict("ERROR", "CLEANUP_UNKNOWN")
    if raw is None:
        return verdict("INCOMPLETE", "REPORT_MISSING")
    if len(raw) > manifest.limits.output_bytes or sha(raw) != record.report_sha256:
        return verdict("ERROR", "REPORT_HASH_OR_SIZE")
    try:

        def unique_keys(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("DUPLICATE_JSON_KEY")
                value[key] = item
            return value

        report = RunnerReport.model_validate(json.loads(raw, object_pairs_hook=unique_keys))
    except (ValidationError, ValueError):
        return verdict("INCOMPLETE", "REPORT_CORRUPT")
    if report.identity != identity:
        return verdict("ERROR", "REPORT_IDENTITY_MISMATCH")
    if not report.complete:
        return verdict("INCOMPLETE", "REPORT_TRUNCATED")
    if report.protected_hash != manifest.test_suite_hash:
        return verdict("ERROR", "PROTECTED_FILES_CHANGED")
    collected = report.collected_ids
    executed = [t.test_id for t in report.tests]
    expected = set(manifest.expected_test_ids)
    if (
        len(collected) != len(set(collected))
        or len(executed) != len(set(executed))
        or set(collected) != expected
        or set(executed) != expected
    ):
        return verdict("INCOMPLETE", "TEST_IDS_MISMATCH")
    if any(t.outcome in {"skipped", "xfail", "xpass"} for t in report.tests):
        return verdict("INCOMPLETE", "REQUIRED_TEST_NOT_PASSED")
    failures = [t.test_id for t in report.tests if t.outcome != "passed"]
    if record.exit_code != (1 if failures else 0):
        return verdict("ERROR", "EXIT_REPORT_CONTRADICTION")
    if any(t.outcome == "error" for t in report.tests):
        return verdict("ERROR", "TEST_EXECUTION_ERROR", failures)
    return (
        verdict("FAIL", "TEST_FAILURE", failures)
        if failures
        else verdict("PASS", "ALL_EXPECTED_TESTS_PASSED")
    )
