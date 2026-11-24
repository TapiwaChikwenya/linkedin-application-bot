"""Make a second Start-Dashboard replace a leftover listener on port 8787."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable

ListenerFn = Callable[[int], list[int]]
CommandFn = Callable[[int], str]
ParentFn = Callable[[int], int]
StopFn = Callable[[int], None]
ProbeFn = Callable[[str, int], bool]


def is_dashboard_command(command: str) -> bool:
    text = (command or "").lower()
    return "linkedin_easy_apply" in text and "--dashboard" in text


def port_is_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def listening_inodes_from_proc(text: str, port: int) -> set[str]:
    """Inodes in LISTEN state for ``port`` from one ``/proc/net/tcp`` table."""
    inodes: set[str] = set()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        if parts[3].upper() != "0A":
            continue
        local = parts[1]
        try:
            local_port = int(local.rsplit(":", 1)[1], 16)
        except (ValueError, IndexError):
            continue
        if local_port == int(port):
            inodes.add(parts[9])
    return inodes


def _posix_listening_pids(port: int) -> list[int]:
    """PIDs listening on ``port``, from /proc. Linux netstat does not end in a PID."""
    inodes: set[str] = set()
    proc_net = "/proc/net"
    for name in ("tcp", "tcp6"):
        try:
            with open(f"{proc_net}/{name}", encoding="utf-8") as handle:
                inodes.update(listening_inodes_from_proc(handle.read(), port))
        except OSError:
            continue
    if not inodes:
        return []
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for name in entries:
        if not name.isdigit():
            continue
        fd_dir = f"/proc/{name}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            if target[8:-1] in inodes:
                pid = int(name)
                if pid not in found:
                    found.append(pid)
                break
    return found


def _windows_listening_pids(port: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[int] = []
    for line in (completed.stdout or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        if not re.search(rf":{int(port)}(?!\d)", line):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in found:
            found.append(pid)
    return found


def listening_pids(port: int) -> list[int]:
    """Return PIDs in LISTENING state for this TCP port. Empty if unknown."""
    if sys.platform == "win32":
        return _windows_listening_pids(port)
    return _posix_listening_pids(port)


def process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if sys.platform != "win32":
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            return ""
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter \"ProcessId="
                + str(int(pid))
                + "\").CommandLine",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "").strip()


def _parent_pid(pid: int) -> int:
    from linkedin_easy_apply.worker import list_processes

    for child, parent, _name in list_processes():
        if child == pid:
            return parent
    return 0


def _stop_pid(pid: int) -> None:
    """Stop one process. Do not kill its children (the worker must keep running)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.kill(pid, 15)


def prepare_dashboard_port(
    host: str,
    port: int,
    *,
    probe: ProbeFn | None = None,
    listeners: ListenerFn | None = None,
    command_line: CommandFn | None = None,
    parent_pid: ParentFn | None = None,
    stop_pid: StopFn | None = None,
    sleep: Callable[[float], None] | None = None,
    wait_seconds: float = 5.0,
) -> dict[str, object]:
    """Free the port when a previous dashboard is still listening.

    A different program on the same port is left alone and reported.
    """
    probe_port = probe or port_is_open
    if not probe_port(host, port):
        return {"ok": True, "replaced": []}
    list_pids = listeners or listening_pids
    read_command = command_line or process_command_line
    parent_of = parent_pid or _parent_pid
    stop = stop_pid or _stop_pid
    pause = sleep or time.sleep
    pids = [pid for pid in list_pids(port) if pid and pid != os.getpid()]
    if not pids:
        return {
            "ok": False,
            "replaced": [],
            "error": (
                "Port "
                + str(port)
                + " is already in use. Close the other Easy Apply console, then start again."
            ),
        }
    for pid in pids:
        if not is_dashboard_command(read_command(pid)):
            return {
                "ok": False,
                "replaced": [],
                "error": (
                    "Port "
                    + str(port)
                    + " is already in use by another program (pid "
                    + str(pid)
                    + "). Close it, then start the dashboard again."
                ),
            }
    to_stop: list[int] = []
    for pid in pids:
        if pid not in to_stop:
            to_stop.append(pid)
        parent = parent_of(pid)
        if (
            parent
            and parent != os.getpid()
            and parent not in to_stop
            and is_dashboard_command(read_command(parent))
        ):
            to_stop.append(parent)
    for pid in to_stop:
        stop(pid)
    deadline = time.time() + max(0.0, wait_seconds)
    while probe_port(host, port):
        if time.time() >= deadline:
            return {
                "ok": False,
                "replaced": to_stop,
                "error": (
                    "Stopped the previous dashboard, but port "
                    + str(port)
                    + " is still in use. Wait a moment and start again."
                ),
            }
        pause(0.2)
    return {"ok": True, "replaced": to_stop}
