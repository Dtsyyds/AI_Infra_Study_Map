"""Local command recorder. Never pass credentials as command-line arguments."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    run = json.loads(Path("TASK_STATUS.json").read_text())["run_id"]
    out = Path("reports") / run
    out.mkdir(parents=True, exist_ok=True)
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    stamp = datetime.now(UTC).strftime("%H%M%S%f")
    log = out / f"{args.name}-{stamp}.log"
    source_paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], text=True
    ).splitlines()
    snapshot = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in source_paths
        if Path(p).is_file()
    }
    snapshot_path = out / f"source-{stamp}.json"
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True, indent=2))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    started = datetime.now(UTC).isoformat()
    tick = time.monotonic()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with log.open("w") as stream:
        result = subprocess.run(argv, env=env, stdout=stream, stderr=subprocess.STDOUT)
    record = dict(
        commit=commit,
        source_manifest=str(snapshot_path),
        command=argv,
        cwd=str(Path.cwd()),
        started_at=started,
        ended_at=datetime.now(UTC).isoformat(),
        elapsed_seconds=time.monotonic() - tick,
        exit_code=result.returncode,
        log=str(log),
    )
    with (out / "commands.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    print(log.read_text(), end="")
    print(json.dumps(record))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
