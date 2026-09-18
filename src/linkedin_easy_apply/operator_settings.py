"""Operator overrides stored in SQLite. Never edit committed config.py."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any

from linkedin_easy_apply.store import Store, default_store_path

DEFAULT_RUN_CAP = 12
DEFAULT_DAY_CAP = 25
MAX_CAP = 500
MAX_SUMMARY_CHARS = 2000
MAX_SNIPPET_CHARS = 500
RESUME_MAX_BYTES = 10 * 1024 * 1024
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RESUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$", re.IGNORECASE)
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SECRET_ENV_KEYS = (
    "LINKEDIN_PASSWORD",
    "LINKEDIN_BOT_PRO_PASSWORD",
    "AngelCoPassword",
    "AngelCoBotPassword",
    "GlobalLogicPassword",
    "GlobalLogicBotPassword",
    "LinkedinBotProPasswrod",
)

SETTING_OLLAMA_MODEL = "ollama_model"
SETTING_RESUME_PATH = "resume_path"
SETTING_SUMMARY = "applicant_summary"
SETTING_RUN_CAP = "max_applications_per_run"
SETTING_DAY_CAP = "max_applications_per_day"
SETTING_SCHEDULE = "schedule_windows"


def _in_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def open_settings_store(store: Store | None = None) -> Store | None:
    if store is not None:
        return store
    if _in_pytest() and not os.getenv("LINKEDIN_STORE_PATH"):
        return None
    path = default_store_path()
    try:
        return Store(path)
    except (OSError, ValueError):
        return None


def load_operator_settings(store: Store | None = None) -> dict[str, str]:
    opened = open_settings_store(store)
    if opened is None:
        return {}
    try:
        return opened.list_settings()
    except (OSError, ValueError):
        return {}


def get_setting(key: str, store: Store | None = None, default: str = "") -> str:
    opened = open_settings_store(store)
    if opened is None:
        return default
    try:
        return opened.get_setting(key, default)
    except (OSError, ValueError):
        return default


def set_setting(key: str, value: str, store: Store | None = None) -> None:
    opened = open_settings_store(store)
    if opened is None:
        raise RuntimeError("operator settings store is not available")
    opened.set_setting(key, value)


def normalize_model_name(value: str) -> str:
    name = " ".join(str(value or "").split()).strip()
    if not name or not MODEL_NAME_RE.match(name):
        raise ValueError("Model name must look like llama3.2, llama3.1, or llama3.2-vision")
    return name


def _positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(number, MAX_CAP)


def resolve_application_caps(
    store: Store | None = None,
    config_module: Any | None = None,
    fallback_run: int | None = None,
    fallback_day: int | None = None,
) -> dict[str, int]:
    settings = load_operator_settings(store)
    run_cap = _positive_int(settings.get(SETTING_RUN_CAP))
    day_cap = _positive_int(settings.get(SETTING_DAY_CAP))
    if run_cap is None:
        run_cap = _positive_int(fallback_run)
    if day_cap is None:
        day_cap = _positive_int(fallback_day)
    if run_cap is None or day_cap is None:
        import config as default_config

        cfg = config_module or default_config
        if run_cap is None:
            run_cap = _positive_int(getattr(cfg, "max_applications_per_run", DEFAULT_RUN_CAP))
        if day_cap is None:
            day_cap = _positive_int(getattr(cfg, "max_applications_per_day", DEFAULT_DAY_CAP))
    return {
        "max_per_run": int(run_cap if run_cap is not None else DEFAULT_RUN_CAP),
        "max_per_day": int(day_cap if day_cap is not None else DEFAULT_DAY_CAP),
    }


def resolved_ollama_model(store: Store | None = None, config_module: Any | None = None) -> str:
    override = str(load_operator_settings(store).get(SETTING_OLLAMA_MODEL) or "").strip()
    if override:
        return override
    import config as default_config

    cfg = config_module or default_config
    return str(
        getattr(cfg, "ollama_model", os.getenv("OLLAMA_MODEL", "llama3.2")) or "llama3.2"
    ).strip() or "llama3.2"


def resolved_resume_path(store: Store | None = None, config_module: Any | None = None) -> str:
    override = str(load_operator_settings(store).get(SETTING_RESUME_PATH) or "").strip()
    if override and os.path.isfile(override):
        return os.path.abspath(override)
    import config as default_config

    cfg = config_module or default_config
    fallback = str(getattr(cfg, "resume_path", "") or "").strip()
    if fallback and os.path.isfile(fallback):
        return os.path.abspath(fallback)
    return fallback


def resolved_applicant_summary(store: Store | None = None) -> str:
    override = str(load_operator_settings(store).get(SETTING_SUMMARY) or "").strip()
    if override:
        return override[:MAX_SUMMARY_CHARS]
    return os.getenv("LINKEDIN_APPLICANT_SUMMARY", "").strip()[:MAX_SUMMARY_CHARS]


def resume_dir(data_dir: str = "data") -> Path:
    folder = Path(data_dir) / "resumes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def safe_resume_name(name: str) -> str:
    raw = Path(str(name or "")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    if cleaned.lower().endswith(".pdf"):
        stem = cleaned[:-4].strip("._") or "resume"
        cleaned = stem + ".pdf"
    else:
        cleaned = (cleaned or "resume") + ".pdf"
    if not RESUME_NAME_RE.match(cleaned):
        cleaned = "resume.pdf"
    return cleaned


def is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:4] == b"%PDF"


def parse_hhmm(value: str) -> time:
    text = str(value or "").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not match:
        raise ValueError("Time must be HH:MM, for example 09:00")
    return time(int(match.group(1)), int(match.group(2)))


def normalize_days(raw: Any) -> list[int]:
    if raw in (None, ""):
        return list(range(7))
    if not isinstance(raw, (list, tuple)):
        raise ValueError("schedule days must be a list")
    days: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            raise ValueError("invalid schedule day")
        if isinstance(item, int):
            number = item
        else:
            token = str(item or "").strip().lower()[:3]
            if token.isdigit():
                number = int(token)
            elif token in DAY_NAMES:
                number = DAY_NAMES.index(token)
            else:
                raise ValueError("Unknown weekday: " + str(item))
        if number < 0 or number > 6:
            raise ValueError("Weekday must be 0-6 (Monday-Sunday)")
        if number not in days:
            days.append(number)
    return days or list(range(7))


def normalize_windows(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("schedule must be JSON") from exc
    if not isinstance(raw, list):
        raise ValueError("schedule must be a list of windows")
    windows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each schedule window must be an object")
        start = parse_hhmm(str(item.get("start") or ""))
        end = parse_hhmm(str(item.get("end") or ""))
        days = normalize_days(item.get("days"))
        windows.append(
            {
                "days": days,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
            }
        )
    return windows


def _window_contains(now: datetime, window: dict[str, Any]) -> bool:
    if now.weekday() not in set(window.get("days") or []):
        return False
    start = parse_hhmm(str(window.get("start") or "00:00"))
    end = parse_hhmm(str(window.get("end") or "00:00"))
    current = now.time().replace(second=0, microsecond=0)
    if start == end:
        return False
    if start < end:
        if end.hour == 23 and end.minute == 59:
            return start <= current
        return start <= current < end
    return current >= start or current < end


def schedule_status(
    store: Store | None = None,
    now: datetime | None = None,
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    try:
        parsed = windows if windows is not None else normalize_windows(
            load_operator_settings(store).get(SETTING_SCHEDULE) or "[]"
        )
    except ValueError as exc:
        return {
            "enabled": False,
            "active": True,
            "reason": "invalid schedule: " + str(exc),
            "windows": [],
            "timezone": str(current.tzname() or "local"),
            "now": current.strftime("%H:%M"),
            "weekday": current.weekday(),
            "window_key": "",
        }
    if not parsed:
        return {
            "enabled": False,
            "active": True,
            "reason": "manual Start only",
            "windows": [],
            "timezone": str(current.tzname() or "local"),
            "now": current.strftime("%H:%M"),
            "weekday": current.weekday(),
            "window_key": "",
        }
    active_window = next((item for item in parsed if _window_contains(current, item)), None)
    if active_window:
        key = (
            current.date().isoformat()
            + "|"
            + ",".join(str(day) for day in active_window["days"])
            + "|"
            + str(active_window["start"])
            + "-"
            + str(active_window["end"])
        )
        return {
            "enabled": True,
            "active": True,
            "reason": "inside schedule",
            "windows": parsed,
            "timezone": str(current.tzname() or "local"),
            "now": current.strftime("%H:%M"),
            "weekday": current.weekday(),
            "window_key": key,
            "window": active_window,
        }
    return {
        "enabled": True,
        "active": False,
        "reason": "outside schedule",
        "windows": parsed,
        "timezone": str(current.tzname() or "local"),
        "now": current.strftime("%H:%M"),
        "weekday": current.weekday(),
        "window_key": "",
    }


def redact_secrets(text: str) -> str:
    value = str(text or "")
    for key in SECRET_ENV_KEYS:
        secret = os.getenv(key) or ""
        if secret:
            value = value.replace(secret, "[redacted]")
    try:
        import config as default_config

        password = str(getattr(default_config, "password", "") or "")
        if password:
            value = value.replace(password, "[redacted]")
    except Exception:  # noqa: BLE001 - config import is best-effort for redaction
        pass
    return value


def apply_operator_overrides(config_module: Any, store: Store | None = None) -> dict[str, Any]:
    payload = public_operator_settings(store, config_module)
    model = str(payload.get("ollama_model") or "").strip()
    if model:
        config_module.ollama_model = model
    resume = str(payload.get("resume_path") or "").strip()
    if resume:
        config_module.resume_path = resume
    caps = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
    if caps.get("max_per_run") is not None:
        config_module.max_applications_per_run = int(caps["max_per_run"])
    if caps.get("max_per_day") is not None:
        config_module.max_applications_per_day = int(caps["max_per_day"])
    return payload


def public_operator_settings(
    store: Store | None = None,
    config_module: Any | None = None,
) -> dict[str, Any]:
    caps = resolve_application_caps(store=store, config_module=config_module)
    schedule = schedule_status(store=store)
    resume = resolved_resume_path(store=store, config_module=config_module)
    return {
        "ollama_model": resolved_ollama_model(store=store, config_module=config_module),
        "resume_path": resume,
        "resume_name": Path(resume).name if resume else "",
        "applicant_summary": resolved_applicant_summary(store=store),
        "quota": caps,
        "schedule": schedule,
    }
