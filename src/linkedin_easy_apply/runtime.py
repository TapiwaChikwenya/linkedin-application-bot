"""Local startup helpers: Firefox path, profile lock, and login window."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any


def firefox_executable() -> str:
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Mozilla Firefox", "firefox.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Mozilla Firefox", "firefox.exe"),
        "/usr/bin/firefox",
        "/Applications/Firefox.app/Contents/MacOS/firefox",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    found = shutil.which("firefox")
    if found:
        return found
    raise FileNotFoundError("Firefox was not found. Install Firefox and try again.")


def is_profile_locked(profile_dir: str) -> bool:
    """Return True when another Firefox process is using the profile directory."""
    if not profile_dir or not os.path.isdir(profile_dir):
        return False
    lock_path = os.path.join(profile_dir, "parent.lock")
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return True


def wait_for_unlocked_profile(profile_dir: str, reader=input, writer=None) -> bool:
    """Ask the operator to close Firefox, then re-check the profile lock."""
    log = writer or (lambda message: print(message, file=sys.stderr))
    if not is_profile_locked(profile_dir):
        return True
    log("Firefox is already using the bot profile.")
    log("Close every Firefox window that opened for this bot, then press Enter.")
    if not sys.stdin.isatty():
        return False
    try:
        reader()
    except EOFError:
        return False
    return not is_profile_locked(profile_dir)


LOGIN_URL = "https://www.linkedin.com/login"
LOGIN_COPY = (
    "Firefox opened at LinkedIn login. Sign in, confirm the feed loads, then "
    "close that Firefox window before Start run."
)
PROFILE_LOCK_LOGIN_ERROR = (
    "Firefox profile is locked. Close other Firefox windows that use this bot "
    "profile, then Login again."
)
WORKER_RUNNING_LOGIN_ERROR = (
    "A worker is already running. Stop it before LinkedIn login — the bot "
    "Firefox profile cannot be shared."
)


def open_profile_login(profile_dir: str, url: str = LOGIN_URL) -> None:
    command = [
        firefox_executable(),
        "-no-remote",
        "-profile",
        profile_dir,
        url,
    ]
    subprocess.Popen(command)


def start_profile_login(profile_dir: str) -> dict[str, Any]:
    """Open the bot Firefox profile at LinkedIn login. Does not start the worker."""
    if not profile_dir:
        return {
            "ok": False,
            "error": "FIREFOX_PROFILE_PATH is required for login.",
        }
    if not os.path.isdir(profile_dir):
        return {
            "ok": False,
            "error": "FIREFOX_PROFILE_PATH is not a directory: " + profile_dir,
        }
    if is_profile_locked(profile_dir):
        return {"ok": False, "error": PROFILE_LOCK_LOGIN_ERROR}
    try:
        open_profile_login(profile_dir)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": "Could not open Firefox: " + str(exc)}
    return {"ok": True, "message": LOGIN_COPY}
