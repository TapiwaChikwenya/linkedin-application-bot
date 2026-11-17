"""Start, probe, and stop a local Ollama sidecar for Easy Apply."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from linkedin_easy_apply.llm import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    clear_status_cache,
    llm_mode,
    ollama_host,
    ollama_model,
)
from linkedin_easy_apply.runlog import event
from linkedin_easy_apply.worker import list_processes, pid_is_alive, read_pid, write_pid

log = logging.getLogger(__name__)

TAGS_WAIT_SECONDS = 90.0
OLLAMA_DOWNLOAD = "https://ollama.com/download"
_OLLAMA_EXES = frozenset({"ollama", "ollama.exe", "ollama app.exe"})
_OLLAMA_IMAGES = ("ollama.exe", "ollama app.exe")
ProbeFn = Callable[[str, float], dict[str, Any]]
WhichFn = Callable[[], str]
PopenFn = Callable[..., subprocess.Popen[bytes]]
ProcessFn = Callable[[], list[tuple[int, int, str]]]
KillFn = Callable[[int], None]


def _exe_name(name: str) -> str:
    return str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def is_ollama_process(name: str) -> bool:
    """True for the serve binary and the Windows tray app that keeps it alive."""
    return _exe_name(name) in _OLLAMA_EXES


def find_ollama_executable() -> str:
    """Return ollama.exe/ollama even when Explorer launches the dashboard without PATH."""
    found = shutil.which("ollama")
    if found:
        return found
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    program_files = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    candidates = [
        local / "Programs" / "Ollama" / "ollama.exe",
        program_files / "Ollama" / "ollama.exe",
        Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
        Path("/usr/bin/ollama"),
        home / ".local" / "bin" / "ollama",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


def model_is_present(models: list[str], model: str) -> bool:
    key = (model or "").split(":")[0]
    if not key:
        return False
    return any(str(name).split(":")[0] == key for name in models)


def probe_ollama(host: str, timeout: float = 1.0) -> dict[str, Any]:
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        names = [str(item.get("name") or "") for item in payload.get("models") or []]
        return {"reachable": True, "models": names, "error": ""}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return {
            "reachable": False,
            "models": [],
            "error": exc.__class__.__name__,
        }


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
    return flags


class OllamaManager:
    """Start, probe, and stop local Ollama, including a server this app did not launch."""

    def __init__(
        self,
        project_root: str,
        host: str | None = None,
        model: str | None = None,
        *,
        probe: ProbeFn | None = None,
        popen: PopenFn | None = None,
        which: WhichFn | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        processes: ProcessFn | None = None,
        killer: KillFn | None = None,
        image_killer: Callable[[], None] | None = None,
    ):
        self.project_root = project_root
        self.host = (host or "").rstrip("/")
        self.model = model or ""
        self.pid_path = os.path.join(project_root, "data", "ollama.pid")
        self.pull_pid_path = os.path.join(project_root, "data", "ollama-pull.pid")
        self.log_path = os.path.join(project_root, "data", "ollama.log")
        self._probe = probe or probe_ollama
        self._popen = popen or subprocess.Popen
        self._which = which or find_ollama_executable
        self._sleep = sleep or time.sleep
        self._clock = clock or time.time
        self._processes = processes or list_processes
        self._killer = killer
        self._image_killer = image_killer
        self._serve: subprocess.Popen[bytes] | None = None
        self._pull: subprocess.Popen[bytes] | None = None
        self._pull_model = ""
        self._log_handle: Any = None
        self.last_detail = ""

    def _resolved_host(self) -> str:
        return self.host or ollama_host()

    def _resolved_model(self) -> str:
        return self.model or ollama_model()

    def _payload(
        self,
        *,
        ok: bool,
        ready: bool,
        detail: str,
        started: bool = False,
        pulling: bool = False,
        pid: int | None = None,
    ) -> dict[str, Any]:
        self.last_detail = detail
        pull_model = self._pull_model or (self._resolved_model() if pulling else "")
        return {
            "ok": ok,
            "ready": ready,
            "host": self._resolved_host(),
            "model": self._resolved_model(),
            "detail": detail,
            "started": started,
            "pulling": pulling,
            "pid": pid,
            "pull_model": pull_model,
            "pull_progress": self.pull_progress() if pulling else "",
        }

    def _owned_pid(self) -> int | None:
        pid = read_pid(self.pid_path)
        if pid and pid_is_alive(pid):
            return pid
        if self._serve is not None and self._serve.poll() is None:
            return int(self._serve.pid)
        return None

    def _pull_running(self) -> bool:
        if self._pull is not None and self._pull.poll() is None:
            return True
        pid = read_pid(self.pull_pid_path)
        return bool(pid and pid_is_alive(pid))

    def _open_log(self) -> Any:
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        handle = open(self.log_path, "ab", buffering=0)  # noqa: SIM115
        self._log_handle = handle
        return handle

    def _spawn(self, command: list[str], pid_path: str) -> subprocess.Popen[bytes]:
        exe = command[0]
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(
            [str(Path(exe).parent), env.get("PATH", "")]
        )
        kwargs: dict[str, Any] = {
            "cwd": self.project_root,
            "env": env,
            "stdout": self._open_log(),
            "stderr": subprocess.STDOUT,
        }
        flags = _creation_flags()
        if flags:
            kwargs["creationflags"] = flags
            kwargs["close_fds"] = False
        proc = self._popen(command, **kwargs)
        write_pid(int(proc.pid), pid_path)
        return proc

    def _start_serve(self) -> dict[str, Any]:
        existing = self._owned_pid()
        if existing:
            return {"ok": True, "pid": existing, "already": True}
        exe = self._which()
        if not exe:
            return {
                "ok": False,
                "error": "Ollama is not installed or not on PATH. Install from "
                + OLLAMA_DOWNLOAD
                + " then Start again.",
            }
        try:
            self._serve = self._spawn([exe, "serve"], self.pid_path)
        except OSError as exc:
            return {"ok": False, "error": "Could not start ollama serve: " + str(exc)}
        return {"ok": True, "pid": int(self._serve.pid), "already": False}

    def _start_pull(self, model: str) -> bool:
        if self._pull_running():
            return True
        exe = self._which()
        if not exe:
            return False
        try:
            self._pull = self._spawn([exe, "pull", model], self.pull_pid_path)
            self._pull_model = model
        except OSError:
            return False
        return True

    def pull_progress(self) -> str:
        try:
            data = Path(self.log_path).read_bytes()[-2000:]
        except OSError:
            return ""
        text = data.decode("utf-8", errors="replace")
        matches = re.findall(r"(\d{1,3})\s*%", text)
        if matches:
            return matches[-1] + "%"
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1][:80] if lines else ""

    def pull(self, model: str) -> dict[str, Any]:
        """Spawn `ollama pull` in the background. Never blocks the dashboard request."""
        name = (model or self._resolved_model() or DEFAULT_MODEL).strip()
        if self._pull_running():
            current = self._pull_model or name
            return self._payload(
                ok=True,
                ready=False,
                detail="pulling model " + current,
                pulling=True,
                pid=self._owned_pid(),
            )
        started = self._start_pull(name)
        if not started:
            return self._payload(
                ok=False,
                ready=False,
                detail="Could not start ollama pull " + name,
                pid=self._owned_pid(),
            )
        return self._payload(
            ok=True,
            ready=False,
            detail="pulling model " + name,
            pulling=True,
            pid=self._owned_pid(),
        )

    def _wait_tags(self, host: str, wait_seconds: float) -> dict[str, Any]:
        deadline = self._clock() + max(0.0, wait_seconds)
        probe = self._probe(host, 1.0)
        while not probe.get("reachable"):
            if self._clock() >= deadline:
                return probe
            self._sleep(0.4)
            probe = self._probe(host, 1.0)
        return probe

    def status(self) -> dict[str, Any]:
        host = self._resolved_host()
        model = self._resolved_model()
        pid = self._owned_pid()
        pulling = self._pull_running()
        pull_model = self._pull_model or (model if pulling else "")
        probe = self._probe(host, 0.6)
        present = bool(probe.get("reachable") and model_is_present(list(probe.get("models") or []), model))
        if pulling:
            progress = self.pull_progress()
            detail = "pulling model " + (pull_model or model)
            if progress:
                detail = detail + " " + progress
        elif present:
            detail = "model present"
        elif probe.get("reachable"):
            detail = "Ollama is up; pull " + model
        else:
            detail = self.last_detail or "Ollama not reachable"
        return {
            "ok": bool(probe.get("reachable")),
            "ready": present,
            "host": host,
            "model": model,
            "detail": detail,
            "started": pid is not None,
            "pulling": pulling,
            "pid": pid,
            "pull_model": pull_model,
            "pull_progress": self.pull_progress() if pulling else "",
        }

    def ensure(self, wait_seconds: float = TAGS_WAIT_SECONDS) -> dict[str, Any]:
        """Bring Ollama up and kick a background pull if the model is missing."""
        host = self._resolved_host()
        model = self._resolved_model() or DEFAULT_MODEL
        if not self.host:
            self.host = host
        if not self.model:
            self.model = model
        if llm_mode() == "off":
            clear_status_cache()
            return self._payload(ok=True, ready=False, detail="disabled")

        probe = self._probe(host, 1.0)
        started = False
        serve_pid = self._owned_pid()
        if not probe.get("reachable"):
            launched = self._start_serve()
            started = bool(launched.get("ok") and not launched.get("already"))
            if launched.get("pid"):
                serve_pid = int(launched["pid"])
            probe = self._wait_tags(host, wait_seconds)
            if not probe.get("reachable"):
                clear_status_cache()
                detail = str(launched.get("error") or "")
                if not detail:
                    detail = (
                        "Ollama did not become ready within "
                        + str(int(wait_seconds))
                        + "s. Install from "
                        + OLLAMA_DOWNLOAD
                        + " then Start run again from the dashboard."
                    )
                event("ollama ensure failed: " + detail, level="error")
                return self._payload(
                    ok=False,
                    ready=False,
                    detail=detail,
                    started=started,
                    pid=serve_pid,
                )

        pulling = False
        models = [str(name) for name in probe.get("models") or []]
        if not model_is_present(models, model):
            pulling = self._start_pull(model)
            detail = "pulling model " + model if pulling else "Ollama is up; pull " + model
            ready = False
        else:
            detail = "model present"
            ready = True
        clear_status_cache()
        event(
            "ollama ensure ok="
            + str(True)
            + " ready="
            + str(ready)
            + " started="
            + str(started)
            + " detail="
            + detail
        )
        return self._payload(
            ok=True,
            ready=ready,
            detail=detail,
            started=started,
            pulling=pulling,
            pid=serve_pid,
        )

    def _kill_pid(self, pid: int) -> None:
        if self._killer is not None:
            self._killer(pid)
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        os.kill(pid, 15)

    def _kill_images(self) -> None:
        """Last resort when the API is up but the process table did not name it."""
        if self._image_killer is not None:
            self._image_killer()
            return
        if sys.platform == "win32":
            for image in _OLLAMA_IMAGES:
                subprocess.run(
                    ["taskkill", "/IM", image, "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return
        subprocess.run(
            ["pkill", "-x", "ollama"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ollama_pids(self) -> list[int]:
        pids: list[int] = []
        owned = self._owned_pid()
        if owned:
            pids.append(owned)
        pull_pid = read_pid(self.pull_pid_path)
        if pull_pid and pull_pid not in pids and pid_is_alive(pull_pid):
            pids.append(pull_pid)
        try:
            rows = self._processes()
        except OSError:
            rows = []
        for pid, _parent, name in rows:
            if is_ollama_process(name) and pid not in pids:
                pids.append(int(pid))
        return pids

    def _clear_pid_files(self) -> None:
        for path in (self.pid_path, self.pull_pid_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not remove %s: %s", path, exc.__class__.__name__)
        self._serve = None
        self._pull = None
        self._pull_model = ""

    def stop(self) -> dict[str, Any]:
        """Stop local Ollama even when the tray app or the operator started it."""
        pids = self._ollama_pids()
        host = self._resolved_host()
        if not pids:
            probe = self._probe(host, 0.6)
            if not probe.get("reachable"):
                self._clear_pid_files()
                clear_status_cache()
                event("ollama stop: not running")
                return {"ok": True, "message": "Ollama is not running.", "stopped": []}
            try:
                self._kill_images()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            self._clear_pid_files()
            clear_status_cache()
            probe = self._probe(host, 0.6)
            if probe.get("reachable"):
                event("ollama stop failed: still reachable", level="error")
                return {"ok": False, "error": "Ollama is still reachable after stop.", "stopped": []}
            event("ollama stop: stopped by image name")
            return {"ok": True, "message": "Ollama stopped.", "stopped": []}
        try:
            for pid in pids:
                self._kill_pid(pid)
        except OSError as exc:
            event("ollama stop failed: " + exc.__class__.__name__, level="error")
            return {"ok": False, "error": str(exc), "stopped": pids}
        self._clear_pid_files()
        clear_status_cache()
        probe = self._probe(host, 0.6)
        if probe.get("reachable"):
            try:
                self._kill_images()
            except OSError as exc:
                return {"ok": False, "error": str(exc), "stopped": pids}
            probe = self._probe(host, 0.6)
        if probe.get("reachable"):
            event("ollama stop failed: still reachable pids=" + ",".join(str(pid) for pid in pids), level="error")
            return {
                "ok": False,
                "error": "Ollama is still reachable after stop.",
                "stopped": pids,
            }
        event("ollama stopped pids=" + ",".join(str(pid) for pid in pids))
        return {"ok": True, "message": "Ollama stopped.", "pid": pids[0], "stopped": pids}


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "OLLAMA_DOWNLOAD",
    "TAGS_WAIT_SECONDS",
    "OllamaManager",
    "find_ollama_executable",
    "is_ollama_process",
    "model_is_present",
    "probe_ollama",
]
