"""Daily and per-run application caps to reduce account-restriction risk."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

DEFAULT_QUOTA_PATH = os.path.join("data", "application_quota.json")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def load_quota(path: str = DEFAULT_QUOTA_PATH) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"date": _today(), "count": 0}
    if payload.get("date") != _today():
        return {"date": _today(), "count": 0}
    try:
        count = int(payload.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    return {"date": _today(), "count": max(0, count)}


def save_quota(payload: dict[str, Any], path: str = DEFAULT_QUOTA_PATH) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def applications_today(path: str = DEFAULT_QUOTA_PATH) -> int:
    return int(load_quota(path)["count"])


def record_application(path: str = DEFAULT_QUOTA_PATH) -> int:
    payload = load_quota(path)
    payload["count"] = int(payload["count"]) + 1
    save_quota(payload, path)
    return int(payload["count"])


def remaining_applications(
    applied_this_run: int,
    max_per_run: int | None = None,
    max_per_day: int | None = None,
    path: str = DEFAULT_QUOTA_PATH,
    store: Any | None = None,
) -> int:
    from linkedin_easy_apply.operator_settings import resolve_application_caps

    caps = resolve_application_caps(
        store=store,
        fallback_run=max_per_run,
        fallback_day=max_per_day,
    )
    run_cap = int(caps["max_per_run"])
    day_cap = int(caps["max_per_day"])
    run_left = max(0, run_cap - applied_this_run)
    day_left = max(0, day_cap - applications_today(path))
    return min(run_left, day_left)
