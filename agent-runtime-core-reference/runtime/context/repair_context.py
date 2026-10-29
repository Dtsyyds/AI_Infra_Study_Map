import copy

from core.contracts.context import ContextBundle
from core.contracts.execution import Execution
from core.errors import RuntimeFault
from runtime.context.repo_map import repo_map
from runtime.repair.manifest import canonical, sha, tree_hash


def candidate_fact(context: dict) -> dict | None:
    candidate = context.get("candidate")
    return (
        {"candidate_no": candidate["candidate_no"], "candidate_hash": candidate["candidate_hash"]}
        if candidate
        else None
    )


class RepairContext:
    async def build(self, execution: Execution) -> ContextBundle:
        ctx = execution.context
        if ctx.get("session_version", execution.session_version) != execution.session_version:
            raise RuntimeFault("SESSION_VERSION_CONFLICT")
        if "manifest" not in ctx or "base_files" not in ctx:
            raise RuntimeFault("REPAIR_SNAPSHOT_MISSING")
        if tree_hash(ctx["base_files"]) != ctx["manifest"]["repo_content_hash"]:
            raise RuntimeFault("BASE_HASH_MISMATCH")
        facts = {
            "manifest_hash": sha(canonical(ctx["manifest"])),
            "base_hash": ctx["manifest"]["repo_content_hash"],
            "image_digest": ctx["manifest"]["image_digest"],
            "test_suite_hash": ctx["manifest"]["test_suite_hash"],
            "policy": ctx["manifest"]["limits"],
            "deadline": execution.deadline_at.isoformat(),
            "task_id": execution.task_id,
        }
        # Accept existing v1 snapshots only after validating every field they carried.
        old = ctx.get("protected_facts")
        if (
            old
            and {k: v for k, v in old.items() if k not in {"candidate", "pending_effects"}} != facts
        ):
            raise RuntimeFault("PROTECTED_FACTS_LOST")
        facts.update(candidate=candidate_fact(ctx), pending_effects=[])
        if old and any(k in old and old[k] != facts[k] for k in ("candidate", "pending_effects")):
            raise RuntimeFault("PROTECTED_FACTS_LOST")
        return ContextBundle(
            rules=[], protected_facts=facts, session_version=execution.session_version
        )

    def observe(self, context: dict, observation: dict) -> dict:
        if len(observation) != 1 or next(iter(observation)) not in {
            "baseline",
            "candidate",
            "verification",
            "report",
        }:
            raise RuntimeFault("CONTEXT_RESERVED_FIELD")
        result = copy.deepcopy(context)
        result.update(copy.deepcopy(observation))
        result["protected_facts"]["candidate"] = candidate_fact(result)
        # Repair tools have no remote business effects; never erase a recorded unresolved effect.
        if result["protected_facts"].get("pending_effects"):
            raise RuntimeFault("PROTECTED_FACTS_LOST")
        result["protected_facts"]["pending_effects"] = []
        # Full evidence is persisted in steps/Artifacts, not repeatedly appended to a prompt.
        return result

    def apply_summary(self, context: dict, summary: dict) -> dict:
        # Only a prose summary may change. Durable candidate/evidence/identity stay untouched.
        if (
            set(summary) != {"summary", "protected_facts"}
            or summary["protected_facts"] != context["protected_facts"]
        ):
            raise RuntimeFault("PROTECTED_FACTS_LOST")
        if not isinstance(summary["summary"], str) or len(summary["summary"].encode()) > 2000:
            raise RuntimeFault("CONTEXT_LIMIT")
        return {**copy.deepcopy(context), "summary": summary["summary"]}


def model_context(execution: Execution, include_map: bool = True) -> dict:
    ctx = execution.context
    manifest, files = ctx["manifest"], ctx["base_files"]
    allowed = manifest["allowed_paths"]
    snippets = []
    for path in allowed:
        text = files[path]
        if len(text.encode()) > 16000:
            raise RuntimeFault("CONTEXT_LIMIT")
        snippets.append(
            {
                "path": path,
                "start_line": 1,
                "end_line": len(text.splitlines()),
                "text": text,
                "hash": sha(text.encode()),
            }
        )
    result = {
        "goal": manifest["goal"],
        "allowed_paths": allowed,
        "code": snippets,
        "protected_facts": ctx["protected_facts"],
        "last_candidate": ctx.get("candidate"),
        "actual_feedback": ctx.get("verification", ctx.get("baseline", {})).get("verdict"),
        "remaining_candidates": manifest["limits"]["candidates"]
        - ctx.get("candidate", {}).get("candidate_no", 0),
    }
    if include_map:
        result["repo_map"] = repo_map(files, allowed)
    if len(canonical(result)) > 24000:
        raise RuntimeFault("CONTEXT_LIMIT")
    return result
