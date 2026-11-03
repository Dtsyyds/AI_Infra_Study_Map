"""W6 实验：比较只结束等待与停止整个工具容器。需要本机 Linux Docker 和 cgroup v2。"""

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import select
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from uuid import uuid4


IMAGE = "python:3.11-slim-bookworm"
CGROUP_ROOT = Path("/sys/fs/cgroup")

# 两组使用相同的固定工具。ready 由已进入新会话的写入进程发出。
# go 表示获准执行一次迟到写入。即使宿主中断，探针本身也有退出期限。
PROBE = r"""
import os, signal, sys, time
from pathlib import Path

out = Path("/evidence")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
writer = os.fork()
if writer == 0:
    os.setsid()
    (out / "ready").touch()
    deadline = time.monotonic() + 90
    while not (out / "go").exists():
        if time.monotonic() >= deadline:
            sys.exit(3)
        time.sleep(0.02)
    (out / "late").write_text("writer ran\n", encoding="utf-8")
    sys.exit(0)
_, status = os.waitpid(writer, 0)
sys.exit(os.waitstatus_to_exitcode(status))
"""


def docker(*args, timeout=20):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"docker {args[0]} 失败 ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def state(cid):
    return json.loads(docker("inspect", "--format", "{{json .State}}", cid))


def open_pidfd(pid):
    """为指定进程打开 pidfd，由调用方关闭。"""
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid, 0)

    # 部分宿主 Python 没有编入该接口，但宿主 glibc 提供相同的调用。
    import ctypes

    try:
        libc_pidfd_open = ctypes.CDLL(None, use_errno=True).pidfd_open
    except AttributeError as exc:
        raise RuntimeError(
            "宿主 Python 和 libc 均未提供 pidfd_open。请用提供 "
            "os.pidfd_open 的宿主 Python 运行本实验。"
        ) from exc
    libc_pidfd_open.argtypes = (ctypes.c_int, ctypes.c_uint)
    libc_pidfd_open.restype = ctypes.c_int
    fd = libc_pidfd_open(pid, 0)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"pidfd_open({pid}): {os.strerror(err)}")
    return fd


def cgroup_of(pid, cid):
    rows = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    path = next((row[3:] for row in rows if row.startswith("0::")), "")
    if not path.startswith("/") or ".." in Path(path).parts or cid not in path:
        raise RuntimeError(
            f"宿主 PID {pid} 未指向本次容器的专属 cgroup。"
            f"请在 Docker 守护进程所在的 Linux 主机运行。读到 {rows}"
        )
    return CGROUP_ROOT / path.lstrip("/")


def populated(group):
    fields = dict(line.split() for line in (group / "cgroup.events").read_text().splitlines())
    return int(fields["populated"])


def wait_empty(group):
    # Docker 可能已删除运行时观测到的专属目录。分别记录空组和目录删除。
    end = monotonic() + 5
    while monotonic() < end:
        try:
            if populated(group) == 0:
                return "populated=0"
        except FileNotFoundError:
            try:
                group.stat()
            except FileNotFoundError:
                return "removed"
        sleep(0.03)
    raise RuntimeError(f"在清理期限内未确认 cgroup 已清空: {group}")


def observe_writer(cid, out):
    end = monotonic() + 10
    while not (out / "ready").exists():
        if monotonic() >= end:
            logs = subprocess.run(
                ["docker", "logs", "--tail", "20", cid],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5,
            )
            raise RuntimeError(f"工具未就绪: {state(cid)}\n{logs.stdout}")
        sleep(0.03)

    leader = state(cid)["Pid"]
    lines = docker("top", cid, "-eo", "pid,ppid,sid").splitlines()[1:]
    processes = [tuple(map(int, line.split())) for line in lines]
    writers = [pid for pid, ppid, sid in processes if ppid == leader and pid == sid]
    if len(writers) != 1:
        raise RuntimeError(f"未找到预期的新会话写入进程: {processes}")
    writer = writers[0]
    group = cgroup_of(leader, cid)
    if cgroup_of(writer, cid) != group or populated(group) != 1:
        raise RuntimeError("启动时主进程与写入进程未同时落在预期的活跃 cgroup")
    return writer, group


@dataclass(frozen=True)
class RunningProbe:
    cid: str
    writer_pid: int
    group: Path
    pidfd: int


@contextmanager
def running_probe(image, out, *extra_labels):
    """在上下文内保留已观察到的容器身份、活跃 cgroup 和写入进程的 pidfd。"""
    label = f"ai-infra-study.w6-timeout={uuid4().hex}"
    label_args = []
    for extra_label in extra_labels:
        label_args.extend(("--label", extra_label))
    cid = None
    try:
        cid = docker(
            "create", "--label", label, *label_args, "--pull", "never", "--init=false", "--restart=no",
            "--network", "none", "--read-only", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--pids-limit", "32", "--memory", "128m", "--cpus", "0.5",
            "--cgroupns", "private", "--stop-signal", "SIGTERM",
            "--mount", f"type=bind,src={out},dst=/evidence",
            "--entrypoint", "python3", image, "-B", "-c", PROBE,
        )
        docker("start", cid)
        writer, group = observe_writer(cid, out)
        pidfd = open_pidfd(writer)
        try:
            if select.select([pidfd], [], [], 0)[0]:
                raise RuntimeError("写入进程在超时实验开始前已经退出")
            yield RunningProbe(cid, writer, group, pidfd)
        finally:
            os.close(pidfd)
    finally:
        # create 与 start 分开。若 create 的回复丢失，只查找本轮的唯一标签。
        owned = [cid] if cid else docker("ps", "-aq", "--filter", f"label={label}").splitlines()
        if owned:
            docker("rm", "-f", "-v", *owned)


def expect_host_timeout(cid):
    try:
        docker("wait", cid, timeout=1)
    except subprocess.TimeoutExpired:
        if not state(cid)["Running"]:
            raise RuntimeError("宿主等待超时时容器已经退出")
    else:
        raise RuntimeError("工具提前结束，未触发预期超时")


def stopped_evidence(probe):
    final = state(probe.cid)
    if final["Status"] != "exited" or final["Running"]:
        raise RuntimeError("尚未观察到容器退出")
    if not select.select([probe.pidfd], [], [], 5)[0]:
        raise RuntimeError("容器已经停止，但启动时记录的写入进程仍未退出")
    return final, wait_empty(probe.group)


def experiment(mode, image, out):
    with running_probe(image, out) as probe:
        print(f"{mode} cid={probe.cid[:12]} host_writer_pid={probe.writer_pid} new_session=yes "
              f"cgroup={probe.group} populated_before=1", flush=True)
        expect_host_timeout(probe.cid)
        if mode == "cleanup":
            docker("stop", "-t", "1", probe.cid)
        (out / "go").touch()
        exit_code = int(docker("wait", probe.cid))
        final, group_end = stopped_evidence(probe)
        expected_code = 0 if mode == "control" else 137
        if exit_code != expected_code or final["ExitCode"] != exit_code or final["OOMKilled"]:
            raise RuntimeError(f"容器退出不符合预期: wait={exit_code}, state={final}")
        late = (out / "late").is_file()
        if late != (mode == "control"):
            raise RuntimeError(f"{mode} 的迟到写入不符合预期: late={late}")
        print(f"{mode} host_wait_timed_out=yes running_at_deadline=yes "
              f"container_exit={exit_code} writer_exited=yes cgroup_end={group_end} "
              f"late_file={'yes' if late else 'no'}", flush=True)


def local_image_id():
    image = docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    if not image.startswith("sha256:"):
        raise RuntimeError(f"未取得本地镜像 ID: {image}")
    return image


def main():
    # 先用当前进程验证宿主能力，成功后再启动实验容器。
    os.close(open_pidfd(os.getpid()))
    image = local_image_id()
    print(f"image={IMAGE} local_id={image}", flush=True)
    with TemporaryDirectory(prefix="month04-w6-", dir=Path.home()) as tmp:
        for mode in ("control", "cleanup"):
            out = Path(tmp) / mode
            out.mkdir()
            # 父临时目录仅宿主用户可进入。容器的映射 UID 只需能写这一个空子目录。
            out.chmod(0o777)
            experiment(mode, image, out)
    print("PASS: 已验证迟到写入的正对照及整容器超时清理")


if __name__ == "__main__":
    main()
