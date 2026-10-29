"""Independent fixed-suite qualification, with gold patches confined to trusted validation."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from core.contracts.repair import Patch, RunRecord, VerificationIdentity
from runtime.repair.harness import verify
from runtime.repair.manifest import canonical, load_case, sha, tree_hash
from runtime.repair.verdict import judge
from runtime.repair.workspace import apply_patch
from runtime.tools.docker_backend import DockerBackend


def current_manifests():
    return {
        f"case-{i:02}": sha(canonical(load_case(f"case-{i:02}")[0].model_dump()))
        for i in range(1, 13)
    }


def qualified(directory):
    try:
        receipt = json.loads((directory / "summary.json").read_text())
        raw = (directory / "results.jsonl").read_bytes()
        rows = [json.loads(line) for line in raw.splitlines()]
        for row in rows:
            manifest, _ = load_case(row["case_id"])
            result = row["result"]
            checked = judge(
                manifest, RunRecord.model_validate(result["record"]), result["raw_report"].encode()
            )
            if checked.model_dump() != result["verdict"] or checked.status != (
                "PASS" if row["mode"] == "correct" else "FAIL"
            ):
                return False
        return (
            receipt["status"] == "PASS"
            and receipt["manifests"] == current_manifests()
            and receipt["raw_sha256"] == sha(raw)
            and len(rows) == 36
            and {(r["case_id"], r["mode"]) for r in rows}
            == {(f"case-{i:02}", m) for i in range(1, 13) for m in ("baseline", "correct", "wrong")}
            and all(r["status"] == "PASS" for r in rows)
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


async def run():
    state = json.loads(await asyncio.to_thread(Path("TASK_STATUS.json").read_text))
    out = Path("reports") / state["run_id"] / "harness-acceptance"
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "results.jsonl"
    if raw.exists() or (out / "summary.json").exists():
        raise RuntimeError("choose new run_id")
    backend = DockerBackend()
    available = await backend.availability()
    summary = {
        "status": "BLOCKED",
        "manifests": current_manifests(),
        "executed": 0,
        "scope": "trusted independent qualification, no model or planner",
    }
    if not available["available"]:
        summary["reason"] = available["reason"]
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
        return
    references = json.loads(
        await asyncio.to_thread(Path("tests/repair/private_reference_patches.json").read_text)
    )
    try:
        for case_id in summary["manifests"]:
            manifest, base = load_case(case_id)
            correct = apply_patch(manifest, base, Patch.model_validate(references[case_id]))
            source = manifest.allowed_paths[0]
            wrong = apply_patch(
                manifest,
                base,
                Patch.model_validate(
                    {
                        "edits": [
                            {
                                "path": source,
                                "old": base[source],
                                "new": base[source] + "\n# intentionally unrepaired\n",
                            }
                        ]
                    }
                ),
            )
            for number, (mode, files) in enumerate(
                (("baseline", base), ("correct", correct), ("wrong", wrong))
            ):
                identity = VerificationIdentity(
                    task_id=str(uuid4()),
                    candidate_no=number,
                    candidate_hash=tree_hash(files),
                    manifest_hash=summary["manifests"][case_id],
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
                expected = "PASS" if mode == "correct" else "FAIL"
                matched = result["verdict"]["status"] == expected
                if mode != "correct":
                    matched = matched and set(result["verdict"]["failed_ids"]) == set(
                        manifest.baseline_fail
                    )
                row = {
                    "case_id": case_id,
                    "mode": mode,
                    "status": "PASS" if matched else "FAIL",
                    "result": result,
                }
                with raw.open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
                summary["executed"] += 1
                if not matched:
                    raise AssertionError("reference qualification failed")
        summary["status"] = "PASS"
    except BaseException:
        summary["status"] = "FAIL"
        raise
    finally:
        if raw.exists():
            summary["raw_sha256"] = sha(raw.read_bytes())
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(run())
