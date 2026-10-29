"""Trusted persistence-only fixture; no candidate execution and no real PASS claims."""

import argparse
import asyncio
from uuid import uuid4

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.repair import CaseManifest, Patch
from runtime.repair.manifest import canonical, tree_hash
from runtime.repair.workspace import apply_patch
from runtime.storage.pool import close_pools
from runtime.storage.store import Store, digest


async def run(task_id, first):
    store = Store(settings.database_url.get_secret_value())
    execution = await store.claim("repair-persistence-probe", 3, task_id)
    assert execution
    if first:
        args = {"candidate_no": 1}
        action = PreparedAction(
            task_id=task_id,
            step_no=0,
            tool_call_id=str(uuid4()),
            tool="repair.generate",
            arguments=args,
            arguments_digest=digest(args),
            effect_class=EffectClass.READ_ONLY,
        )
        await store.prepare(execution, action)
        await store.dispatch(execution, action)
        manifest = CaseManifest.model_validate(execution.context["manifest"])
        patch = Patch.model_validate(
            {
                "edits": [
                    {
                        "path": "src/solution.py",
                        "old": "max(low, x)",
                        "new": "min(high, max(low, x))",
                    }
                ]
            }
        )
        files = apply_patch(manifest, execution.context["base_files"], patch)
        result = {
            "candidate_no": 1,
            "patch": patch.model_dump(),
            "candidate_hash": tree_hash(files),
            "scope": "synthetic storage fixture; no model or test execution",
        }
        await store.checkpoint(
            execution, action, result, {**execution.context, "candidate": result}, canonical(result)
        )
        await store.barrier("repair-patch:" + task_id, execution)
    else:
        assert execution.checkpoint == 0 and execution.context["candidate"]["candidate_no"] == 1
        await store.finish(
            execution, {"business_outcome": "UNRESOLVED", "scope": "storage probe only"}
        )
    await close_pools()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--first", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.task_id, args.first))
