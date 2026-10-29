"""Read-only local development capacity check; does not start services or load secrets."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
ROOT = Path(__file__).resolve().parents[1]


def inspect(check_docker: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, observed: Any, guidance: str) -> None:
        checks.append(dict(name=name, status=status, observed=observed, guidance=guidance))

    disk = shutil.disk_usage(ROOT)
    disk_status = "BLOCKED" if disk.free < 5 * GIB else "CAUTION" if disk.free < 20 * GIB else "OK"
    add("workspace_disk", disk_status, {"free_gib": round(disk.free / GIB, 2)},
        "Development recommendation: >=20 GiB free, preferably 30-50 GiB; no automatic cleanup.")
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    add("logical_cpus", "OK" if cpus and cpus >= 4 else "CAUTION", cpus,
        "Start with one task and one verification container; this command does not set limits.")
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        total = int(info["MemTotal"].split()[0]) * 1024
        available = int(info["MemAvailable"].split()[0]) * 1024
        add("host_memory", "OK" if total >= 8 * GIB and available >= 2 * GIB else "CAUTION",
            {"total_gib": round(total / GIB, 2), "available_gib": round(available / GIB, 2)},
            "Host snapshot only; cgroup/container limits are not measured. 16 GB class recommended.")
    except (OSError, KeyError, ValueError):
        add("host_memory", "CAUTION", None, "Memory unavailable; verify capacity manually.")
    add("python", "OK" if sys.version_info[:2] == (3, 11) else "BLOCKED",
        platform.python_version(), "Use this repository's .venv/bin/python (Python 3.11).")
    for name, relative in (("postgres_binary", ".postgres/bin/pg_ctl"),
                           ("virtualenv_python", ".venv/bin/python")):
        present = os.access(ROOT / relative, os.X_OK)
        add(name, "OK" if present else "CAUTION", present, f"Existing local layout: {relative}")
    if check_docker:
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            version = result.stdout.strip()
            reachable = result.returncode == 0 and bool(version) and version != "<no value>"
            add("docker_daemon", "OK" if reachable else "BLOCKED",
                {"reachable": reachable, "server_version": version if reachable else None,
                 "exit_code": result.returncode},
                "Requires an authorized Docker session. Does not verify images or sandbox execution.")
        except (OSError, subprocess.TimeoutExpired):
            add("docker_daemon", "BLOCKED", None,
                "Docker missing, inaccessible, or timed out; container state remains unknown.")
    else:
        add("docker_daemon", "NOT_CHECKED", None, "Use --docker for a read-only daemon probe.")
    status = "BLOCKED" if any(c["status"] == "BLOCKED" for c in checks) else (
        "CAUTION" if any(c["status"] in {"CAUTION", "NOT_CHECKED"} for c in checks) else "READY"
    )
    return {"observed_at": datetime.now(UTC).isoformat(), "workspace": str(ROOT),
            "scope": "local development advisory, not integration acceptance", "status": status,
            "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true", help="Probe Docker without starting containers")
    parser.add_argument("--json", action="store_true", help="Print machine-readable observations")
    parser.add_argument("--strict", action="store_true", help="Exit 2 on warnings or unchecked items")
    args = parser.parse_args()
    report = inspect(args.docker)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Development preflight: {report['status']}")
        for check in report["checks"]:
            print(f"[{check['status']}] {check['name']}: {check['observed']}")
            if check["status"] != "OK":
                print(f"  {check['guidance']}")
    return 2 if report["status"] == "BLOCKED" or (args.strict and report["status"] != "READY") else 0


if __name__ == "__main__":
    raise SystemExit(main())
