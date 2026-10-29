"""Read-only consistency check for a completed load run; no runtime or model calls."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from examples.experiment_metrics import TERMINAL, summarize_experiment, task_timings


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def verify_records(plan, rows, events):
    levels = plan["concurrency"]
    require(len(levels) == len(set(levels)), "DUPLICATE_CONCURRENCY_LEVEL")
    expected = {(c, r, i) for c in levels for r in range(1, plan["rounds"] + 1)
                for i in range(plan["submissions_per_round"])}
    require(len(expected) == plan["total"] == len(rows), "INCOMPLETE_RECORDS")
    require({(r["concurrency"], r["round"], r["index"]) for r in rows} == expected,
            "SLOT_MISMATCH")
    identities = [r["submission_id"] for r in rows]
    require(len(identities) == len(set(identities)), "DUPLICATE_SUBMISSION")
    by_id = defaultdict(list)
    for event in events:
        by_id[event["submission_id"]].append(event)
    require(set(by_id) == set(identities), "UNRESOLVED_JOURNAL_IDENTITY")
    tasks = set()
    for row in rows:
        journal = by_id[row["submission_id"]]
        accepted = row["http_status"] == 202
        expected_events = ["SUBMISSION_INTENT"] + (["ACCEPTED"] if accepted else []) + ["OBSERVATION_FINISHED"]
        require([e["event"] for e in journal] == expected_events, "JOURNAL_SEQUENCE_MISMATCH")
        intent, finish = journal[0], journal[-1]
        require(intent["model"] is False, "MODEL_IN_DETERMINISTIC_LOAD")
        require(intent["request"]["session_id"] == row["submission_id"], "REQUEST_IDENTITY_MISMATCH")
        require(intent["slot"] == {k: row[k] for k in ("index", "concurrency", "round")}, "JOURNAL_SLOT_MISMATCH")
        require(finish["status"] == row["status"] and finish["task_id"] == row["task_id"]
                and finish["record_file"] == "load_raw.jsonl", "JOURNAL_RESULT_MISMATCH")
        require(not row.get("observation_incomplete") and row["status"] != "OBSERVATION_ERROR", "INCOMPLETE_OBSERVATION")
        if not accepted:
            require(row["task_id"] is None and row["status"] in {"REJECTED", "HTTP_ERROR"}, "REJECTION_MISMATCH")
            continue
        task_id = row["task_id"]
        require(task_id and task_id not in tasks, "DUPLICATE_TASK")
        tasks.add(task_id)
        require(journal[1]["task_id"] == task_id, "ACCEPTED_IDENTITY_MISMATCH")
        diag = row["diagnostics"]
        task = diag["task"]
        require(task["task_id"] == task_id and task["session_id"] == row["submission_id"], "DIAGNOSTIC_IDENTITY_MISMATCH")
        require(all(task["request"].get(k) == v for k, v in intent["request"].items()), "DIAGNOSTIC_REQUEST_MISMATCH")
        require(task["status"] == row["status"] == row["runtime_status"] and row["status"] in TERMINAL, "NONTERMINAL_OR_MISMATCHED_RESULT")
        require(row["timings"] == task_timings(diag), "TIMING_MISMATCH")
        require(not diag.get("model_calls"), "MODEL_IN_DETERMINISTIC_LOAD")
        artifacts = row.get("artifacts", [])
        require([{k: v for k, v in a.items() if k != "file"} for a in artifacts] == diag.get("artifacts", []), "ARTIFACT_INVENTORY_MISMATCH")
        require(row["status"] != "SUCCEEDED" or bool(artifacts), "SUCCESS_WITHOUT_ARTIFACT")
    return {"integrity_status": "PASS", "submissions": len(rows), "accepted_tasks": len(tasks),
            "journal_events": len(events), "unresolved_journal_identities": 0,
            "outcomes": dict(Counter(r["status"] for r in rows)),
            "observations_with_errors": sum(bool(r.get("errors")) for r in rows),
            "scope": "completed deterministic load evidence consistency; not external acceptance or production capacity"}


def verify(directory):
    plan = json.loads((directory / "load_config.json").read_text())
    rows = [json.loads(line) for line in (directory / "load_raw.jsonl").read_text().splitlines()]
    events = [json.loads(line) for line in (directory / "load_raw_events.jsonl").read_text().splitlines()]
    result = verify_records(plan, rows, events)
    artifact_ids = set()
    for row in rows:
        if row["task_id"]:
            filename = f"worker-{row['task_id']}.log"
            require(Path(filename).name == filename, "UNSAFE_EVIDENCE_PATH")
            require(not (directory / filename).is_symlink() and (directory / filename).is_file(), "MISSING_WORKER_LOG")
        for artifact in row.get("artifacts", []):
            # Relocatable within its evidence directory; never follow a raw path outside it.
            filename = f"artifact-{artifact['artifact_id']}.json"
            require(Path(filename).name == filename and Path(artifact["file"]).name == filename, "UNSAFE_EVIDENCE_PATH")
            require(artifact["artifact_id"] not in artifact_ids, "DUPLICATE_ARTIFACT")
            artifact_ids.add(artifact["artifact_id"])
            path = directory / filename
            require(not path.is_symlink() and path.is_file(), "MISSING_ARTIFACT")
            content = path.read_bytes()
            require(len(content) == artifact["bytes"] and hashlib.sha256(content).hexdigest() == artifact["sha256"], "ARTIFACT_HASH_MISMATCH")
    summary = summarize_experiment(rows, kind="load", planned=plan["total"])
    require(summary == json.loads((directory / "load_summary.json").read_text()), "SUMMARY_MISMATCH")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2))
