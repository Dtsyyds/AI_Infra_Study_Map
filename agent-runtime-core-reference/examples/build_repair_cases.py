"""Author original synthetic assets as text; never execute candidate code on host."""

import json
from pathlib import Path
from typing import Literal

from core.contracts.repair import CaseManifest
from runtime.repair.manifest import approved_argv, runner_source, sha, tree_hash
from runtime.tools.docker_backend import IMAGE

# Source, erroneous text, correction, failing assertion, regression assertion, goal.
CASES = [
    (
        "def clamp(x, low, high):\n    return max(low, x)\n",
        "max(low, x)",
        "min(high, max(low, x))",
        "self.assertEqual(clamp(20, 0, 10), 10)",
        "self.assertEqual(clamp(-2, 0, 10), 0)",
        "clamp must respect both inclusive bounds",
    ),
    (
        "def parse_flag(value):\n    return bool(value)\n",
        "bool(value)",
        'value.strip().lower() in {"true", "1"}',
        'self.assertFalse(parse_flag("false"))',
        'self.assertTrue(parse_flag("true"))',
        "parse true/false and 1/0 textual flags",
    ),
    (
        'def total(rows):\n    return sum(row["amount"] for row in rows)\n',
        'row["amount"]',
        'row.get("amount", 0)',
        "self.assertEqual(total([{}]), 0)",
        'self.assertEqual(total([{"amount": 2}]), 2)',
        "missing amount contributes zero",
    ),
    (
        "def chunks(items, size):\n    return [items[i:i+size] for i in range(0, len(items)-size+1, size)]\n",
        "len(items)-size+1",
        "len(items)",
        "self.assertEqual(chunks([1,2,3], 2), [[1,2],[3]])",
        "self.assertEqual(chunks([1,2], 2), [[1,2]])",
        "include final partial chunk for positive size",
    ),
    (
        "def encode(value):\n    import json\n    return str(value)\n",
        "str(value)",
        "json.dumps(value, sort_keys=True)",
        'self.assertEqual(encode({"x": True}), \'{"x": true}\')',
        'self.assertEqual(encode(1), "1")',
        "encode valid stable JSON",
    ),
    (
        "def unique(values):\n    return list(set(values))\n",
        "list(set(values))",
        "list(dict.fromkeys(values))",
        "self.assertEqual(unique([3,1,3,2]), [3,1,2])",
        "self.assertEqual(unique([]), [])",
        "deduplicate while retaining first occurrence order",
    ),
    (
        "def median(values):\n    values = sorted(values)\n    return values[len(values)//2]\n",
        "values[len(values)//2]",
        "(values[(len(values)-1)//2] + values[len(values)//2]) / 2",
        "self.assertEqual(median([1,3]), 2)",
        "self.assertEqual(median([1,2,3]), 2)",
        "median of a nonempty sequence handles even lengths",
    ),
    (
        "def get_header(headers, name):\n    return headers.get(name)\n",
        "headers.get(name)",
        "next((v for k,v in headers.items() if k.lower() == name.lower()), None)",
        'self.assertEqual(get_header({"Content-Type":"json"}, "content-type"), "json")',
        'self.assertIsNone(get_header({}, "x"))',
        "HTTP header lookup is case insensitive",
    ),
    (
        "async def read_or_default(reader):\n    try:\n        return reader()\n    except ValueError:\n        return None\n",
        "return reader()",
        "return await reader()",
        "self.assertIsNone(asyncio.run(read_or_default(bad)))",
        "self.assertEqual(asyncio.run(read_or_default(good)), 3)",
        "await asynchronous reader and catch ValueError",
    ),
    (
        "def offset(page, size):\n    return page * size\n",
        "page * size",
        "(page - 1) * size",
        "self.assertEqual(offset(1, 20), 0)",
        "self.assertEqual(offset(2, 0), 0)",
        "one-based pagination begins at offset zero",
    ),
    (
        "def merge_defaults(defaults, supplied):\n    return {**supplied, **defaults}\n",
        "{**supplied, **defaults}",
        "{**defaults, **supplied}",
        'self.assertEqual(merge_defaults({"x":1},{"x":2})["x"], 2)',
        'self.assertEqual(merge_defaults({"x":1},{}), {"x":1})',
        "explicit values override defaults without mutation",
    ),
    (
        "def seconds(milliseconds):\n    return milliseconds // 1000\n",
        "milliseconds // 1000",
        "milliseconds / 1000",
        "self.assertEqual(seconds(1500), 1.5)",
        "self.assertEqual(seconds(2000), 2)",
        "retain fractional precision converting milliseconds",
    ),
]


def main():
    references, suite = {}, []
    for index, (source, old, new, failure, regression, goal) in enumerate(CASES, 1):
        case_id = f"case-{index:02}"
        split: Literal["dev", "holdout"] = "dev" if index <= 4 else "holdout"
        extra = (
            'import asyncio\nasync def bad():\n    raise ValueError("bad")\nasync def good():\n    return 3\n'
            if index == 9
            else ""
        )
        tests = f"import unittest\nfrom solution import *\n{extra}\nclass Case(unittest.TestCase):\n    def test_bug(self):\n        {failure}\n    def test_regression(self):\n        {regression}\n"
        files = {"src/solution.py": source, "tests/test_case.py": tests}
        manifest = CaseManifest(
            case_id=case_id,
            split=split,
            goal=goal,
            snapshot_hash=tree_hash(files),
            repo_content_hash=tree_hash(files),
            image_digest=IMAGE,
            test_suite_hash=tree_hash({"tests/test_case.py": tests}),
            runner_hash=sha(runner_source().encode()),
            allowed_paths=["src/solution.py"],
            forbidden_paths=[
                "tests",
                "conftest.py",
                "pytest.ini",
                "pyproject.toml",
                "requirements.lock",
                "runtime",
                "core",
                "deploy",
            ],
            expected_test_ids=["test_case.Case.test_bug", "test_case.Case.test_regression"],
            baseline_fail=["test_case.Case.test_bug", "test_case.Case.test_regression"]
            if index == 9
            else ["test_case.Case.test_bug"],
            baseline_pass=[] if index == 9 else ["test_case.Case.test_regression"],
            test_argv=approved_argv(),
        )
        directory = Path("examples/repair_cases") / case_id
        for name, content in files.items():
            p = directory / "repo" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2))
        references[case_id] = {"edits": [{"path": "src/solution.py", "old": old, "new": new}]}
        suite.append(
            {
                "case_id": case_id,
                "split": split,
                "manifest_hash": sha(manifest.model_dump_json().encode()),
            }
        )
    Path("tests/repair/private_reference_patches.json").write_text(json.dumps(references, indent=2))
    Path("examples/repair_cases/suite.json").write_text(
        json.dumps(
            {
                "source": "original synthetic teaching cases",
                "license": "CC0-1.0",
                "blind_evaluation": False,
                "split_frozen_before_model_requests": True,
                "cases": suite,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
