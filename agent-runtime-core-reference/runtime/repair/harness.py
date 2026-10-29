"""Planner-independent verifier: accepts a prepared candidate tree and trusted identity."""

from core.contracts.repair import CaseManifest, RunRecord, VerificationIdentity
from runtime.repair.manifest import canonical, sha, tree_hash, validate_policy
from runtime.repair.verdict import judge
from runtime.tools.docker_backend import DockerBackend, DockerUnavailable


async def verify(
    backend: DockerBackend,
    manifest: CaseManifest,
    files: dict[str, str],
    identity: VerificationIdentity,
    *,
    attempt_id: str,
    fence: int,
    remaining_seconds: float,
    test_pause: bool = False,
    on_test_started=None,
) -> dict:
    validate_policy(manifest)
    if tree_hash(files) != identity.candidate_hash:
        raise ValueError("CANDIDATE_HASH_MISMATCH")
    if (
        tree_hash({p: v for p, v in files.items() if p not in manifest.allowed_paths})
        != manifest.test_suite_hash
    ):
        raise ValueError("PROTECTED_FILES_CHANGED")
    raw = None
    record = RunRecord(identity=identity, started=False, exit_code=None)
    evidence = {}
    try:
        evidence = await backend.run_fixed(
            manifest.test_argv[-1],
            canonical(
                {
                    "files": files,
                    "identity": identity.model_dump(),
                    "allowed_paths": manifest.allowed_paths,
                    "test_pause": test_pause,
                }
            ),
            task_id=identity.task_id,
            attempt_id=attempt_id,
            fence=fence,
            run_id=identity.verification_run_id,
            wall_seconds=min(manifest.limits.wall_seconds, remaining_seconds),
            output_bytes=manifest.limits.output_bytes,
            on_test_started=on_test_started,
        )
        output = evidence["stdout"]
        record.started = output.startswith("ARC_REPAIR_STARTED\n")
        record.exit_code = evidence["exit_code"]
        record.timed_out = evidence["timed_out"]
        record.resource_error = evidence["resource_error"]
        record.cleanup_confirmed = evidence["cleanup_confirmed"]
        lines = [
            line.removeprefix("ARC_REPAIR_REPORT ")
            for line in output.splitlines()
            if line.startswith("ARC_REPAIR_REPORT ")
        ]
        if len(lines) == 1:
            raw = lines[0].encode()
            record.report_sha256 = sha(raw)
    except DockerUnavailable:
        evidence = {
            "blocked": "DOCKER_UNAVAILABLE",
            "cleanup": "UNKNOWN",
            "image_digest": backend.image,
        }
    verdict = judge(manifest, record, raw)
    return {
        "record": record.model_dump(),
        "raw_report": raw.decode() if raw else None,
        "verdict": verdict.model_dump(),
        "container_evidence": evidence,
    }
