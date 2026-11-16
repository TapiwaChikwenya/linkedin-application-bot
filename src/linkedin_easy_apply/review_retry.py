"""Decide which needs_review jobs can be opened again after the operator answers."""

from __future__ import annotations

import re
from typing import Any

_QUESTION_RE = re.compile(r"unanswered required question:\s*(.+)", re.IGNORECASE)


def question_from_review_reason(reason: str) -> str:
    """Pull the field label out of a stored skip reason. Empty when it is not one."""
    match = _QUESTION_RE.search(str(reason or ""))
    if not match:
        return ""
    question = " ".join(match.group(1).split())
    for marker in (" Job:", " Link:", ". Link"):
        if marker in question:
            question = question.split(marker)[0]
    return question.strip(" .")


def _temporal_ready(question: str, store: Any, config_module: Any) -> bool | None:
    """True/False when the question is a date or month/year. None when it is not."""
    from linkedin_easy_apply.operator_settings import (
        resolved_earliest_start_date,
        resolved_work_history,
    )
    from linkedin_easy_apply.page_snapshot import control_shape, is_availability_date

    label = question.casefold()
    field: dict[str, Any] = {"question": question, "kind": "text", "required": True, "value": ""}
    if label == "month":
        field["kind"] = "select"
        field["options"] = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    elif label == "year":
        field["kind"] = "select"
        field["options"] = [str(year) for year in range(2024, 2014, -1)]
    shape = control_shape(field)
    if shape == "date" or is_availability_date(field):
        return bool(resolved_earliest_start_date(store, config_module))
    if shape == "month":
        history = resolved_work_history(store, config_module)
        if any(str(item.get("start_month") or "") for item in history):
            return True
        return bool(str(getattr(config_module, "work_history_month", "") or "").strip())
    if shape == "year":
        history = resolved_work_history(store, config_module)
        if any(str(item.get("start_year") or "") for item in history):
            return True
        return bool(str(getattr(config_module, "work_history_year", "") or "").strip())
    if label == "work history":
        return bool(resolved_work_history(store, config_module))
    return None


def review_question_is_answerable(question: str, store: Any, config_module: Any) -> bool:
    """True when a later run can fill this required field without guessing."""
    text = " ".join(str(question or "").split())
    if not text:
        return False
    temporal = _temporal_ready(text, store, config_module)
    if temporal is not None:
        return temporal
    if store is not None:
        retrieve = getattr(store, "retrieve_approved_answers", None)
        if callable(retrieve):
            try:
                matches = retrieve([{"question": text, "kind": "text"}], limit=1) or []
            except (OSError, TypeError, ValueError):
                matches = []
            if matches:
                return True
    lowered = text.casefold()
    if "linkedin" in lowered and "year" not in lowered:
        return bool(str(getattr(config_module, "LinkedInProfileURL", "") or "").strip())
    if "phone" in lowered:
        return bool(str(getattr(config_module, "phone_number", "") or "").strip())
    if "city" in lowered or lowered == "location":
        return bool(str(getattr(config_module, "application_city", "") or "").strip())
    return False


def retryable_jobs(store: Any, config_module: Any, limit: int = 25) -> list[dict[str, Any]]:
    """needs_review rows whose unanswered question now has a grounded answer."""
    if store is None:
        return []
    lister = getattr(store, "list_jobs", None)
    if not callable(lister):
        return []
    try:
        rows = lister(status="needs_review", limit=max(1, int(limit))) or []
    except (OSError, TypeError, ValueError):
        return []
    ready: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not str(row.get("job_id") or "").strip():
            continue
        question = question_from_review_reason(str(row.get("reason") or ""))
        if review_question_is_answerable(question, store, config_module):
            ready.append(row)
    return ready
