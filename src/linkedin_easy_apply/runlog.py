"""Daily UTF-8 log for the dashboard, scheduler, Ollama sidecar, and worker.

One file per local calendar day: ``data/logs/YYYY-MM-DD.log``. Job lines,
schedule decisions, and Ollama start/stop land here so a later pass can read
what happened without reconstructing the console.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()
_CONFIGURED: set[str] = set()


def project_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def daily_log_path(root: str | None = None, now: datetime | None = None) -> Path:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return Path(root or project_root()) / "data" / "logs" / (current.date().isoformat() + ".log")


def _redact(message: str) -> str:
    try:
        from linkedin_easy_apply.operator_settings import redact_secrets

        return redact_secrets(message)
    except Exception:  # noqa: BLE001 - logging must not fail closed
        return message


def configure_logging(root: str | None = None, *, role: str = "app") -> str:
    """Attach one UTF-8 file handler for this process. Safe to call twice."""
    path = daily_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path.resolve())
    logger = logging.getLogger("linkedin_easy_apply")
    logger.setLevel(logging.INFO)
    with _LOCK:
        if target not in _CONFIGURED:
            handler = logging.FileHandler(target, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
            )
            logger.addHandler(handler)
            _CONFIGURED.add(target)
    event("process logging started role=" + role + " file=" + target)
    return target


def event(message: str, *, level: str = "info") -> None:
    """Write one redacted line. Caller configures the file handler first."""
    text = _redact(str(message or "")).replace("\r", " ").replace("\n", " | ")
    logger = logging.getLogger("linkedin_easy_apply.runlog")
    log_level = level if level in {"debug", "info", "warning", "error"} else "info"
    getattr(logger, log_level)("%s", text)
