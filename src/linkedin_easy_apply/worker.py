"""Start and stop the Selenium worker as a child process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from linkedin_easy_apply.runtime import is_profile_locked
from linkedin_easy_apply.store import Store

PID_PATH = os.path.join("data", "worker.pid")
STATE_PATH = os.path.join("data", "worker.json")
HEARTBEAT_TIMEOUT_SECONDS = 45.0
STARTUP_GRACE_SECONDS = 90.0
CLAIM_WAIT_SECONDS = 45.0
PROFILE_LOCK_ERROR = (
    "Firefox profile is locked. Close other Firefox windows that use this bot "
    "profile, then Start again."
)
AliveCheck = Callable[[int], bool]
ProcessRow = tuple[int, int, str]


def resolve_worker_python(project_root: str) -> str:
    """Prefer the project venv so Start works from a dashboard launched via .cmd."""
    root = Path(project_root)
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    if candidate.is_file():
        return str(candidate)
    return sys.executable


def worker_launch_env(project_root: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Put venv Scripts and Ollama on PATH; keep src importable if the package is editable."""
    env = dict(base or os.environ)
    root = Path(project_root)
    extras: list[str] = []
    venv_bin = root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    if venv_bin.is_dir():
        extras.append(str(venv_bin))
        env.setdefault("VIRTUAL_ENV", str(root / ".venv"))
    try:
        from linkedin_easy_apply.ollama_runtime import find_ollama_executable

        ollama_exe = find_ollama_executable()
    except ImportError:
        ollama_exe = ""
    if ollama_exe:
        extras.append(str(Path(ollama_exe).parent))
    env["PATH"] = os.pathsep.join([*extras, env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(root / "src"), str(root), env.get("PYTHONPATH", "")])
    )
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _worker_creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    flags = int(subprocess.CREATE_NEW_PROCESS_GROUP)
    flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return flags


def pid_is_alive(pid: int) -> bool:
    """Return True when the OS still has this process.

    Access-denied on Windows is treated as alive: the process exists, but this
    dashboard may not be allowed to open it. Missing/invalid PIDs are dead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if handle:
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return int(exit_code.value) == still_active
    return ctypes.GetLastError() == error_access_denied


def list_processes() -> list[ProcessRow]:
    """Return (pid, parent_pid, executable name) for the local process table."""
    if sys.platform == "win32":
        return _windows_processes()
    return _posix_processes()


def _windows_processes() -> list[ProcessRow]:
    import ctypes
    from ctypes import wintypes

    th32cs_snapprocess = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        rows: list[ProcessRow] = []
        while True:
            rows.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile or ""),
                )
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return rows
    finally:
        kernel32.CloseHandle(snapshot)


def _posix_processes() -> list[ProcessRow]:
    rows: list[ProcessRow] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return rows
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            comm_start = stat_text.find("(")
            comm_end = stat_text.rfind(")")
            if comm_start < 0 or comm_end <= comm_start:
                continue
            name = stat_text[comm_start + 1 : comm_end]
            fields = stat_text[comm_end + 2 :].split()
            ppid = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
        rows.append((int(entry.name), ppid, name))
    return rows


def _exe_base(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _is_worker_exe(name: str) -> bool:
    base = _exe_base(name)
    return base.startswith(("python", "geckodriver"))


def descendant_rows(root_pid: int, processes: list[ProcessRow] | None = None) -> list[ProcessRow]:
    if root_pid <= 0:
        return []
    snapshot = list_processes() if processes is None else processes
    children: dict[int, list[ProcessRow]] = {}
    for pid, ppid, name in snapshot:
        children.setdefault(ppid, []).append((pid, ppid, name))
    found: list[ProcessRow] = []
    stack = [root_pid]
    seen = {root_pid}
    while stack:
        current = stack.pop()
        for row in children.get(current, []):
            pid = row[0]
            if pid in seen:
                continue
            seen.add(pid)
            found.append(row)
            stack.append(pid)
    return found


def find_owned_worker_pid(
    root_pid: int,
    *,
    alive_check: AliveCheck | None = None,
    processes: list[ProcessRow] | None = None,
) -> int | None:
    """Return a live python/geckodriver descendant of an owned root PID."""
    check = alive_check or pid_is_alive
    python_pid = None
    gecko_pid = None
    for pid, _ppid, name in descendant_rows(root_pid, processes):
        if not _is_worker_exe(name) or not check(pid):
            continue
        if _exe_base(name).startswith("python"):
            python_pid = python_pid or pid
        else:
            gecko_pid = gecko_pid or pid
    return python_pid or gecko_pid


def _int_pid(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def recorded_pids(state: dict[str, Any] | None) -> list[int]:
    if not state:
        return []
    pids: list[int] = []
    for key in ("pid", "launcher_pid"):
        pid = _int_pid(state.get(key))
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def resolve_live_owned_pid(
    state: dict[str, Any] | None,
    *,
    now: float | None = None,
    alive_check: AliveCheck | None = None,
    processes: list[ProcessRow] | None = None,
) -> int | None:
    """Return the owned python/geckodriver PID that should drive live status.

    A short-lived launcher PID is not enough to declare a crash. Heartbeat from
    the worker lease, a still-living recorded PID, or a python/geckodriver
    descendant of that owned tree all count. Unrelated Firefox processes do not.
    """
    if not state:
        return None
    check = alive_check or pid_is_alive
    heartbeat_ok = False
    try:
        heartbeat = float(state["heartbeat"])
        age = (time.time() if now is None else now) - heartbeat
        heartbeat_ok = 0 <= age <= HEARTBEAT_TIMEOUT_SECONDS
    except (KeyError, TypeError, ValueError):
        heartbeat_ok = False

    pids = recorded_pids(state)
    for pid in pids:
        if check(pid):
            return pid

    snapshot = list_processes() if processes is None else processes
    for root in pids:
        child = find_owned_worker_pid(root, alive_check=check, processes=snapshot)
        if child:
            return child

    if heartbeat_ok:
        worker_pid = _int_pid(state.get("pid"))
        if worker_pid > 0:
            return worker_pid
        # Launcher is still starting and has not claimed a worker PID yet.
        return pids[0] if pids else None
    try:
        started_at = float(state.get("started_at") or 0)
    except (TypeError, ValueError):
        started_at = 0.0
    clock = time.time() if now is None else now
    if not snapshot and started_at and 0 <= clock - started_at <= STARTUP_GRACE_SECONDS:
        # Process enumeration failed during startup; do not false-crash yet.
        worker_pid = _int_pid(state.get("pid"))
        return worker_pid or (pids[0] if pids else None)
    return None


def state_is_live(
    state: dict[str, Any] | None,
    *,
    now: float | None = None,
    alive_check: AliveCheck | None = None,
    processes: list[ProcessRow] | None = None,
) -> bool:
    return resolve_live_owned_pid(
        state, now=now, alive_check=alive_check, processes=processes
    ) is not None


def read_pid(path: str = PID_PATH) -> int | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_pid(pid: int, path: str = PID_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(pid))


def clear_pid(path: str = PID_PATH) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return


def read_state(path: str = STATE_PATH) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return state if isinstance(state, dict) else None


def write_state(state: dict[str, Any], path: str = STATE_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        target.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp"
    )
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def clear_state(path: str = STATE_PATH, token: str = "") -> None:
    if token:
        state = read_state(path)
        if state and state.get("token") != token:
            return
    try:
        os.remove(path)
    except FileNotFoundError:
        return


def merge_launcher_state(
    existing: dict[str, Any] | None,
    *,
    launcher_pid: int,
    token: str,
    run_id: str,
) -> dict[str, Any]:
    """Keep a worker-claimed PID when the dashboard records its launcher."""
    claimed_pid = _int_pid(existing.get("pid")) if existing else 0
    if existing and existing.get("token") == token and claimed_pid > 0:
        merged = dict(existing)
        merged["launcher_pid"] = launcher_pid
        merged["run_id"] = existing.get("run_id") or run_id
        return merged
    return {
        "heartbeat": time.time(),
        "launcher_pid": launcher_pid,
        "pid": 0,
        "run_id": run_id,
        "started_at": time.time(),
        "token": token,
    }


@contextmanager
def worker_process_lease():
    """Publish the PID and heartbeat of the Python process doing the real work."""
    path = os.environ.get("LINKEDIN_WORKER_STATE_PATH", "")
    token = os.environ.get("LINKEDIN_WORKER_TOKEN", "")
    run_id = os.environ.get("LINKEDIN_RUN_ID", "")
    if not path or not token:
        yield
        return

    stop_event = threading.Event()
    existing = read_state(path) or {}
    state = {
        "heartbeat": time.time(),
        "launcher_pid": _int_pid(existing.get("launcher_pid")),
        "pid": os.getpid(),
        "run_id": run_id or str(existing.get("run_id") or ""),
        "started_at": existing.get("started_at") or time.time(),
        "token": token,
    }

    def heartbeat() -> None:
        while not stop_event.wait(2.0):
            state["heartbeat"] = time.time()
            write_state(state, path)

    write_state(state, path)
    thread = threading.Thread(target=heartbeat, name="worker-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=3.0)
        clear_state(path, token)


class WorkerManager:
    def __init__(self, store: Store, project_root: str | None = None):
        self.store = store
        self.project_root = project_root or os.getcwd()
        self.process: subprocess.Popen[bytes] | None = None
        self.pid_path = os.path.join(self.project_root, "data", "worker.pid")
        self.state_path = os.path.join(self.project_root, "data", "worker.json")
        self.log_path = os.path.join(self.project_root, "data", "worker.log")
        self.token = ""
        self.last_error = ""
        self._log_handle: Any = None
        self._start_lock = threading.Lock()

    def _state_with_launcher(self, state: dict[str, Any] | None) -> dict[str, Any] | None:
        if self.process is None or self.process.poll() is not None:
            return state
        merged = dict(state or {})
        merged.setdefault("launcher_pid", self.process.pid)
        merged.setdefault("heartbeat", time.time())
        if "pid" not in merged:
            merged["pid"] = 0
        return merged

    def _phase(self, alive: bool, state: dict[str, Any] | None, pid: int | None, run: dict[str, Any] | None) -> str:
        if not alive:
            if run and str(run.get("status") or "") == "crashed":
                return "crashed"
            return "stopped"
        claimed = _int_pid((state or {}).get("pid"))
        launcher = _int_pid((state or {}).get("launcher_pid"))
        if claimed > 0 and pid_is_alive(claimed):
            return "running"
        if pid and pid != launcher:
            return "running"
        return "starting"

    def status(self) -> dict[str, Any]:
        state = self._state_with_launcher(read_state(self.state_path))
        pid = resolve_live_owned_pid(state)
        if pid is None:
            fallback = read_pid(self.pid_path)
            if fallback and state is not None:
                probe = dict(state)
                probe.setdefault("launcher_pid", fallback)
                pid = resolve_live_owned_pid(probe)
                if pid is not None:
                    state = probe
        alive = pid is not None
        if not alive:
            recorded = recorded_pids(state) or [read_pid(self.pid_path) or 0]
            if recorded[0]:
                clear_pid(self.pid_path)
            clear_state(self.state_path)
            running = self.store.running_run()
            if running:
                self.last_error = self.last_error or (
                    "Worker process is gone. The run was marked crashed."
                )
                self.store.finish_run(running["run_id"], "crashed")
        elif state and state.get("run_id"):
            self.store.restore_running_run(str(state["run_id"]))
        run = self.store.running_run() if alive else self.store.current_run()
        claimed = _int_pid((state or {}).get("pid")) if state else 0
        return {
            "alive": alive,
            "pid": pid if alive else None,
            "claimed_pid": claimed if claimed > 0 else None,
            "phase": self._phase(alive, state, pid, run),
            "last_error": self.last_error,
            "run": run,
        }

    def start(
        self,
        profile_dir: str = "",
        *,
        wait_seconds: float = CLAIM_WAIT_SECONDS,
        poll_interval: float = 0.2,
    ) -> dict[str, Any]:
        if not self._start_lock.acquire(blocking=False):
            error = "Start is already in progress."
            self.last_error = error
            return {"ok": False, "error": error}
        try:
            return self._start_locked(profile_dir, wait_seconds, poll_interval)
        finally:
            self._start_lock.release()

    def _start_locked(
        self,
        profile_dir: str,
        wait_seconds: float,
        poll_interval: float,
    ) -> dict[str, Any]:
        current = self.status()
        if current["alive"]:
            error = "A worker is already running."
            self.last_error = error
            return {"ok": False, "error": error}
        if profile_dir and is_profile_locked(profile_dir):
            self.last_error = PROFILE_LOCK_ERROR
            return {"ok": False, "error": PROFILE_LOCK_ERROR}
        python_exe = resolve_worker_python(self.project_root)
        if not os.path.isfile(python_exe):
            error = (
                "Python interpreter not found at "
                + python_exe
                + ". Create .venv in the project folder."
            )
            self.last_error = error
            return {"ok": False, "error": error}

        run_id = self.store.start_run()
        token = uuid.uuid4().hex
        env = worker_launch_env(self.project_root)
        env["LINKEDIN_RUN_ID"] = run_id
        env["LINKEDIN_WORKER_TOKEN"] = token
        env["LINKEDIN_WORKER_STATE_PATH"] = self.state_path
        command = [python_exe, "-u", "-m", "linkedin_easy_apply"]
        os.makedirs(os.path.join(self.project_root, "data"), exist_ok=True)
        try:
            self._log_handle = open(self.log_path, "wb")  # noqa: SIM115
        except OSError:
            self._log_handle = subprocess.DEVNULL
        kwargs: dict[str, Any] = {
            "cwd": self.project_root,
            "env": env,
            "stdout": self._log_handle,
            "stderr": subprocess.STDOUT,
        }
        flags = _worker_creation_flags()
        if flags:
            kwargs["creationflags"] = flags
        try:
            self.process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            self._close_log()
            return self._abort_start(
                run_id,
                "Could not start worker Python (" + python_exe + "): " + str(exc),
            )
        self.token = token
        write_pid(self.process.pid, self.pid_path)
        write_state(
            merge_launcher_state(
                read_state(self.state_path),
                launcher_pid=self.process.pid,
                token=token,
                run_id=run_id,
            ),
            self.state_path,
        )
        return self._wait_for_claimed_pid(
            run_id,
            int(self.process.pid),
            wait_seconds,
            poll_interval,
        )

    def _wait_for_claimed_pid(
        self,
        run_id: str,
        launcher_pid: int,
        wait_seconds: float,
        poll_interval: float,
    ) -> dict[str, Any]:
        deadline = time.time() + max(0.0, wait_seconds)
        while True:
            state = read_state(self.state_path) or {}
            claimed = _int_pid(state.get("pid"))
            if claimed > 0 and pid_is_alive(claimed):
                self.last_error = ""
                return {
                    "ok": True,
                    "pid": claimed,
                    "launcher_pid": launcher_pid,
                    "run_id": run_id,
                }
            state["heartbeat"] = time.time()
            state.setdefault("launcher_pid", launcher_pid)
            state.setdefault("run_id", run_id)
            state.setdefault("token", self.token)
            write_state(state, self.state_path)

            poll = self.process.poll() if self.process is not None else 0
            if poll is not None:
                owned = resolve_live_owned_pid(state)
                if owned and pid_is_alive(owned) and claimed == 0:
                    if time.time() >= deadline:
                        break
                    time.sleep(poll_interval)
                    continue
                if claimed > 0 and pid_is_alive(claimed):
                    self.last_error = ""
                    return {
                        "ok": True,
                        "pid": claimed,
                        "launcher_pid": launcher_pid,
                        "run_id": run_id,
                    }
                return self._abort_start(run_id, self._start_failure_message(poll))
            if time.time() >= deadline:
                break
            time.sleep(poll_interval)
        return self._abort_start(
            run_id,
            "Worker did not claim a live PID in time. Check data/worker.log, the "
            "project .venv, and that Firefox is not using this profile.",
        )

    def _start_failure_message(self, code: int | None) -> str:
        tail = self._log_tail()
        combined = tail.lower()
        if "firefox" in combined and ("profile" in combined or "using" in combined):
            return PROFILE_LOCK_ERROR
        if "no module named" in combined:
            return (
                "Worker Python could not import linkedin_easy_apply. From the "
                "project folder run: .venv\\Scripts\\python.exe -m pip install -e ."
            )
        if tail:
            return "Worker exited (code " + str(code) + "): " + tail[-400:]
        return (
            "Worker process exited before it claimed a PID (code "
            + str(code)
            + "). Check .venv and PATH."
        )

    def _log_tail(self, limit: int = 1200) -> str:
        try:
            data = Path(self.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return data[-limit:].strip()

    def _close_log(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle is not None and handle is not subprocess.DEVNULL:
            try:
                handle.close()
            except OSError:
                pass

    def _kill_tree(self, pid: int) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        os.kill(pid, signal.SIGTERM)

    def _abort_start(self, run_id: str, error: str) -> dict[str, Any]:
        self.last_error = error
        state = self._state_with_launcher(read_state(self.state_path))
        pid = self._stop_pid(state)
        try:
            if pid:
                self._kill_tree(pid)
            elif self.process is not None and self.process.pid:
                self._kill_tree(int(self.process.pid))
        except OSError:
            pass
        self._close_log()
        self.process = None
        clear_pid(self.pid_path)
        clear_state(self.state_path, self.token)
        running = self.store.running_run()
        if running:
            self.store.finish_run(running["run_id"], "crashed")
        elif run_id:
            current = self.store.current_run()
            if current and current.get("run_id") == run_id and current.get("status") == "running":
                self.store.finish_run(run_id, "crashed")
        return {"ok": False, "error": error}

    def _stop_pid(self, state: dict[str, Any] | None) -> int | None:
        resolved = resolve_live_owned_pid(state)
        if resolved and pid_is_alive(resolved):
            return resolved
        for root in recorded_pids(state):
            if pid_is_alive(root):
                return root
            child = find_owned_worker_pid(root)
            if child:
                return child
        return resolved

    def stop(self) -> dict[str, Any]:
        state = self._state_with_launcher(read_state(self.state_path))
        pid = self._stop_pid(state)
        if not pid:
            self._close_log()
            clear_pid(self.pid_path)
            clear_state(self.state_path)
            running = self.store.running_run()
            if running:
                self.store.finish_run(running["run_id"], "stopped")
            return {"ok": True, "message": "No worker was running."}
        try:
            self._kill_tree(pid)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        self._close_log()
        self.process = None
        clear_pid(self.pid_path)
        clear_state(self.state_path, str(state.get("token") or "") if state else "")
        running = self.store.running_run()
        if running:
            self.store.finish_run(running["run_id"], "stopped")
        return {"ok": True, "pid": pid}
