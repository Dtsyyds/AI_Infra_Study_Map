"""Apply bounded exact replacements as DATA. Never import/execute candidate source."""

import difflib

from core.contracts.repair import CaseManifest, Patch
from runtime.repair.manifest import canonical, tree_hash


def apply_patch(manifest: CaseManifest, base: dict[str, str], patch: Patch) -> dict[str, str]:
    if tree_hash(base) != manifest.repo_content_hash:
        raise ValueError("BASE_HASH_MISMATCH")
    if len(canonical(patch.model_dump())) > manifest.limits.patch_bytes:
        raise ValueError("PATCH_BYTES_EXCEEDED")
    paths = [edit.path for edit in patch.edits]
    if len(paths) != len(set(paths)) or len(paths) > manifest.limits.files:
        raise ValueError("PATCH_FILES_EXCEEDED_OR_DUPLICATE")
    result = dict(base)
    for edit in patch.edits:
        if edit.path not in manifest.allowed_paths or edit.path not in base:
            raise ValueError("PATCH_PATH_FORBIDDEN")
        if "\x00" in edit.old + edit.new or base[edit.path].count(edit.old) != 1:
            raise ValueError("PATCH_NOT_EXACT_TEXT")
        result[edit.path] = base[edit.path].replace(edit.old, edit.new, 1)
    changed = sum(
        1
        for path in paths
        for line in difflib.ndiff(base[path].splitlines(), result[path].splitlines())
        if line.startswith(("+ ", "- "))
    )
    if changed == 0 or changed > manifest.limits.changed_lines:
        raise ValueError("PATCH_LINES_INVALID")
    if (
        tree_hash({p: v for p, v in result.items() if p not in manifest.allowed_paths})
        != manifest.test_suite_hash
    ):
        raise ValueError("PROTECTED_FILES_CHANGED")
    return result
