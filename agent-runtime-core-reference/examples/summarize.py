"""Recomputable statistics; keep status groups and incomplete denominators explicit."""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def summarize(records):
    groups = defaultdict(list)
    for row in records:
        result = row.get("result", row)
        name = row.get("scenario", row.get("group", "load"))
        status = result.get("status", row.get("status", "UNKNOWN"))
        groups[(name, status)].append(row)
    output = []
    for (name, status), rows in sorted(groups.items()):
        values = sorted(
            row.get("elapsed_seconds", row.get("submission_wall_seconds"))
            for row in rows
            if row.get("elapsed_seconds", row.get("submission_wall_seconds")) is not None
        )
        output.append(
            {
                "group": name,
                "status": status,
                "n": len(rows),
                "p50_seconds": values[math.ceil(len(values) * 0.5) - 1] if values else None,
                "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
                "max_seconds": max(values) if values else None,
                "small_sample_p95_unstable": len(values) < 100,
            }
        )
    return {
        "observed_records": len(records),
        "groups": output,
        "counts": dict(Counter(row.get("scenario", row.get("group", "load")) for row in records)),
        "warning": "probe elapsed time is not task execution latency; rejected results are a separate group",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line]
    Path(args.output).write_text(json.dumps(summarize(rows), indent=2))


if __name__ == "__main__":
    main()
