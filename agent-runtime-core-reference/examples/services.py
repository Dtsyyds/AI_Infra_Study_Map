"""只管理本项目创建并记录的进程；PID 文件不参与任务恢复。"""
import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "reports" / "services.json"


def owned(pid: int) -> bool | None:
    try:
        return Path(f"/proc/{pid}/cwd").readlink() == ROOT and "uvicorn" in Path(f"/proc/{pid}/cmdline").read_text()
    except FileNotFoundError:
        return False
    except PermissionError:
        return None


def start() -> None:
    if STATE.exists():
        ownership = [owned(x["pid"]) for x in json.loads(STATE.read_text())]
        if None in ownership:
            raise RuntimeError("service ownership UNKNOWN; use the group session that started it")
        if any(ownership):
            print(STATE.read_text())
            return
    services: list[dict[str, Any]] = []
    try:
        for name, module, port in [("arc-ref-api", "api.main:app", 18081),
                                   ("arc-ref-business", "examples.business_service:app", 18082)]:
            with socket.socket() as check:
                check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                check.bind(("127.0.0.1", port))
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            # 故障控制仅由可信环境开启，不在普通API请求中接受。
            env["ARC_TEST_MODE"] = "true"
            log = ROOT / "reports" / f"{name}.log"
            with log.open("a") as stream:
                process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", module,
                                            "--host", "127.0.0.1", "--port", str(port), "--limit-concurrency", "32"],
                                           cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            services.append({"name": name, "port": port, "pid": process.pid})
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"{name} failed; see {log}")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.05)
            else:
                raise RuntimeError(f"{name} startup timeout")
    finally:
        if services:
            STATE.write_text(json.dumps(services, indent=2))
    print(STATE.read_text())


def stop() -> None:
    if not STATE.exists():
        print("no recorded services")
        return
    services = json.loads(STATE.read_text())
    for service in services:
        pid = service["pid"]
        ownership = owned(pid)
        if ownership is None:
            service.update(stopped=None, ownership="UNKNOWN")
            STATE.write_text(json.dumps(services, indent=2))
            raise RuntimeError("service ownership UNKNOWN; use the group session that started it")
        if ownership:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(100):
                if owned(pid) is False:
                    break
                time.sleep(0.05)
            if owned(pid) is True:
                os.killpg(pid, signal.SIGKILL)
                for _ in range(100):
                    if owned(pid) is False:
                        break
                    time.sleep(0.05)
        service["stopped"] = True if owned(pid) is False else None
    STATE.write_text(json.dumps(services, indent=2))
    print(STATE.read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    if args.action == "start":
        start()
    elif args.action == "stop":
        stop()
    else:
        print(STATE.read_text() if STATE.exists() else "[]")


if __name__ == "__main__":
    main()
