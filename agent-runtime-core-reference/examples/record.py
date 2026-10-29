"""Run a command with real duration/exit code, retaining stdout for repeatable evidence."""
import argparse
import datetime as dt
import json
import subprocess
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="20261022-reference")
    parser.add_argument("--name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    directory = Path("reports") / args.run
    directory.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.UTC).isoformat()
    tick = time.monotonic()
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_name = f"{args.name}.log"
    if (directory / output_name).exists():
        output_name = f"{args.name}-{dt.datetime.now(dt.UTC).strftime('%H%M%S%f')}.log"
    (directory / output_name).write_text(result.stdout)
    record = {"command": command, "started_at": started, "elapsed_seconds": time.monotonic()-tick,
              "exit_code": result.returncode, "output": output_name}
    with (directory / "commands.log").open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(result.stdout, end="")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
