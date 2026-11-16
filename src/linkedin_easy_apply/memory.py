"""Standing memory compiled from operator-approved answers.

Ollama weights stay frozen. The learning loop is: approve a row, then every
later form prompt receives a compact ``standing_memory`` slice. A new approval
is included on the next call. Answers reused on a successful application rank
higher after that. Pending rows are not learned.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from linkedin_easy_apply.question_capture import normalize_question

STANDING_MEMORY_LIMIT = 16
STANDING_RECENT = 8
_QUESTION_CAP = 180
_VALUE_CAP = 160
_SKIP_KINDS = frozenset({"file", "password"})


def _kind(row: dict[str, Any]) -> str:
    return str(
        row.get("classified_kind") or row.get("field_kind") or row.get("kind") or ""
    ).strip().lower()


def _approved_value(row: dict[str, Any]) -> str:
    return " ".join(str(row.get("approved_value") or row.get("value") or "").split())


def _source_id(row: dict[str, Any]) -> str:
    ident = row.get("id")
    if ident in (None, ""):
        ident = row.get("question_id") or row.get("source_id")
    return str(ident or "")


def _question_text(row: dict[str, Any]) -> str:
    text = (
        row.get("cleaned_question")
        or row.get("raw_question")
        or row.get("question")
        or row.get("question_text")
        or row.get("normalized_question")
        or ""
    )
    return " ".join(str(text).split())


def _eligible(row: dict[str, Any]) -> bool:
    if not _approved_value(row) or not _question_text(row):
        return False
    if _kind(row) in _SKIP_KINDS:
        return False
    if row.get("not_user_answerable"):
        return False
    merged = str(row.get("merged_into_id") or "").strip()
    if merged not in {"", "0", "None"}:
        return False
    status = str(row.get("approval_status") or row.get("status") or "approved").strip().lower()
    return status in {"", "approved"}


def _identity(row: dict[str, Any]) -> str:
    cluster = str(row.get("cluster_key") or "").strip().lower()
    if cluster:
        return "cluster:" + cluster
    return "q:" + normalize_question(_question_text(row))


def _priority(row: dict[str, Any]) -> tuple[int, str, int]:
    try:
        reuse = int(row.get("reuse_count") or 0)
    except (TypeError, ValueError):
        reuse = 0
    try:
        ident = int(row.get("id") or 0)
    except (TypeError, ValueError):
        ident = 0
    return (reuse, str(row.get("last_seen_at") or ""), ident)


def _compact(row: dict[str, Any]) -> dict[str, str]:
    value = _approved_value(row)[:_VALUE_CAP]
    proposed = " ".join(str(row.get("proposed_value") or "").split())
    item = {
        "source_id": _source_id(row),
        "question": _question_text(row)[:_QUESTION_CAP],
        "value": value,
        "reuse_count": str(_priority(row)[0]),
    }
    if proposed and proposed.casefold() != value.casefold():
        item["corrected"] = "true"
    return item


def compile_standing_memory(
    rows: list[dict[str, Any]] | None,
    *,
    limit: int = STANDING_MEMORY_LIMIT,
    recent: int = STANDING_RECENT,
    exclude_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """Recent approvals plus the most reused confirmed answers.

    Newest approvals are reserved first so a label is in the next prompt even
    when older phone or email rows have a much higher reuse count. The rest of
    the slice is filled by reuse. One row per question or cluster.
    """
    cap = max(0, int(limit))
    if cap == 0:
        return []
    excluded = {str(item) for item in (exclude_ids or []) if str(item).strip()}
    best: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict) or not _eligible(row):
            continue
        ident = _source_id(row)
        if ident and ident in excluded:
            continue
        key = _identity(row)
        current = best.get(key)
        if current is None or _priority(row) > _priority(current):
            best[key] = row
    unique = list(best.values())
    by_recent = sorted(
        unique,
        key=lambda row: (str(row.get("last_seen_at") or ""), _priority(row)[2]),
        reverse=True,
    )
    by_reuse = sorted(unique, key=_priority, reverse=True)
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    recent_cap = min(max(0, int(recent)), cap)

    def take(row: dict[str, Any]) -> None:
        ident = _source_id(row) or _identity(row)
        if ident in seen:
            return
        seen.add(ident)
        picked.append(row)

    for row in by_recent:
        if len(picked) >= recent_cap:
            break
        take(row)
    for row in by_reuse:
        if len(picked) >= cap:
            break
        take(row)
    return [_compact(row) for row in picked[:cap]]


def standing_memory_for_store(
    store: Any | None,
    *,
    exclude_ids: list[str] | None = None,
    limit: int = STANDING_MEMORY_LIMIT,
) -> list[dict[str, str]]:
    """Read approved rows and compile the prompt slice. Empty when the store is down."""
    if store is None:
        return []
    listed = getattr(store, "list_approved_questions", None)
    if not callable(listed):
        return []
    try:
        rows = list(listed(limit=500) or [])
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return []
    return compile_standing_memory(rows, limit=limit, exclude_ids=exclude_ids)


def memory_status(store: Any | None) -> dict[str, Any]:
    """Counts for the Questions page. Does not include answer text."""
    status = {
        "approved": 0,
        "pending": 0,
        "in_prompt": 0,
        "reuses": 0,
        "weights_frozen": True,
        "learns_on": "approve",
    }
    if store is None:
        return status
    try:
        counter = getattr(store, "question_counts", None)
        counts = dict(counter() or {}) if callable(counter) else {}
        status["approved"] = int(counts.get("approved") or 0)
        status["pending"] = int(counts.get("pending") or 0)
        listed = getattr(store, "list_approved_questions", None)
        rows = list(listed(limit=500) or []) if callable(listed) else []
        status["reuses"] = sum(int(row.get("reuse_count") or 0) for row in rows)
        status["in_prompt"] = len(compile_standing_memory(rows))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return status
    return status
