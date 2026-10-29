"""Real candidates run only via Docker. BLOCKED skips are not PASS."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from core.contracts.repair import Patch, VerificationIdentity
from runtime.repair.harness import verify
from runtime.repair.manifest import canonical, load_case, sha, tree_hash
from runtime.repair.workspace import apply_patch
from runtime.tools.docker_backend import DockerBackend
from tests.docker.test_backend import require_real_docker


@pytest.mark.parametrize("case_no", range(1, 13))
async def test_baseline_and_reference_patch_in_real_sandbox(case_no):
    backend = DockerBackend()
    await require_real_docker(backend)
    case_id = f"case-{case_no:02}"
    manifest, base = load_case(case_id)
    patch = Patch.model_validate(
        json.loads(await asyncio.to_thread(Path("tests/repair/private_reference_patches.json").read_text))[case_id]
    )
    for number, files in [(0, base), (1, apply_patch(manifest, base, patch))]:
        identity = VerificationIdentity(
            task_id=str(uuid4()),
            candidate_no=number,
            candidate_hash=tree_hash(files),
            manifest_hash=sha(canonical(manifest.model_dump())),
            verification_run_id=str(uuid4()),
            nonce=uuid4().hex,
            image_digest=manifest.image_digest,
            test_suite_hash=manifest.test_suite_hash,
            repo_content_hash=manifest.repo_content_hash,
            runner_hash=manifest.runner_hash,
        )
        result = await verify(
            backend,
            manifest,
            files,
            identity,
            attempt_id=str(uuid4()),
            fence=1,
            remaining_seconds=15,
        )
        assert result["verdict"]["status"] == ("PASS" if number else "FAIL"), result
        if number == 0:
            assert set(result["verdict"]["failed_ids"]) == set(manifest.baseline_fail)
