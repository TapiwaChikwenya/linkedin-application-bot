"""Capture Easy Apply screening questions for operator review and later retrieval.

JSONL remains a value-free audit trail. SQLite `questions` rows are the durable
memory/RAG store: proposed and approved answers live there, not in model weights.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CAPTURE_PATH = os.path.join("data", "question_capture.jsonl")

QUESTION_SOURCES = ("mapped", "llm", "unanswered", "manual", "seed")
SKIP_KINDS = frozenset({"file", "password"})
SELECT_KINDS = frozenset({"select", "radio"})
PLACEHOLDER_OPTIONS = frozenset(
    {
        "",
        "select",
        "select an option",
        "please select",
        "[email]",
        "[phone]",
        "[redacted-email]",
        "[redacted-phone]",
        "[redacted_email]",
        "[redacted_phone]",
    }
)
MAX_VALUE_CHARS = 400
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")


def _redact(text: object) -> str:
    cleaned = str(text or "")
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    return _PHONE_RE.sub("[REDACTED_PHONE]", cleaned)


def normalize_question(raw: str) -> str:
    """Stable identity for a screening prompt: casefold, dash-fold, collapse space."""
    text = str(raw or "")
    for src, dst in (("\u2011", "-"), ("\u2013", "-"), ("\u2014", "-"), ("\u00a0", " ")):
        text = text.replace(src, dst)
    text = " ".join(text.casefold().split())
    return text.strip(" :*-")


def option_set_hash(kind: str, options: list[Any] | None) -> str:
    """Hash select/radio option sets so different choice lists stay distinct rows."""
    if str(kind or "").lower() not in SELECT_KINDS:
        return ""
    cleaned: list[str] = []
    for option in options or []:
        text = " ".join(str(option).casefold().split())
        if text and text not in PLACEHOLDER_OPTIONS:
            cleaned.append(text)
    if not cleaned:
        return ""
    blob = "|".join(sorted(set(cleaned)))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def should_skip_field(field: dict[str, Any]) -> bool:
    kind = str(field.get("kind") or "").lower()
    input_type = str(field.get("type") or "").lower()
    if kind in SKIP_KINDS or input_type == "password":
        return True
    return not str(field.get("question") or "").strip()


def redact_proposed_value(kind: str, value: str) -> str:
    """Never persist raw email/phone answers in proposed_value."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    kind_l = str(kind or "").lower()
    if kind_l in {"email"}:
        return "[redacted-email]"
    if kind_l in {"tel", "phone"}:
        return "[redacted-phone]"
    if len(text) > MAX_VALUE_CHARS:
        return text[:MAX_VALUE_CHARS]
    return text


def classify_source(field: dict[str, Any]) -> str:
    from linkedin_easy_apply.page_snapshot import unanswered_fields

    if str(field.get("_llm_value") or "").strip():
        return "llm"
    if field.get("_approved_fill"):
        return "manual"
    if field.get("_mapped_value_present"):
        return "mapped"
    if field.get("_answered") and field.get("_llm_considered"):
        return "llm"
    meta = {key: value for key, value in field.items() if key != "_element"}
    if unanswered_fields([meta]):
        return "unanswered"
    if field.get("_answered"):
        return "mapped"
    return "unanswered"


def proposed_value_for(field: dict[str, Any], source: str) -> str:
    if source == "llm":
        raw = str(field.get("_llm_value") or field.get("value") or "")
    elif source == "mapped":
        raw = str(field.get("value") or "")
    else:
        raw = ""
    return redact_proposed_value(str(field.get("kind") or ""), raw)


def source_confidence(source: str) -> float | None:
    if source == "mapped":
        return 1.0
    if source == "llm":
        return 0.5
    return None


def source_provenance(source: str) -> str:
    if source == "mapped":
        return "config"
    if source == "llm":
        return "ollama"
    if source == "manual":
        return "operator"
    if source == "seed":
        return "seed"
    return "observed"


def append_questions(
    fields: list[dict[str, Any]],
    *,
    job_id: object,
    title: str = "",
    company: str = "",
    path: str = DEFAULT_CAPTURE_PATH,
) -> int:
    """Append one redacted record per field; never persist answer values."""
    records = []
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for field in fields:
        question = _redact(field.get("question"))
        if not question:
            continue
        records.append(
            {
                "ts": timestamp,
                "job_id": str(job_id),
                "title": _redact(title),
                "company": _redact(company),
                "question": question,
                "kind": str(field.get("kind") or ""),
                "options": [_redact(option) for option in field.get("options") or []],
                "required": bool(field.get("required")),
                "mapped_value_present": bool(field.get("_mapped_value_present")),
                "llm_used": bool(field.get("_llm_considered")),
                "filled": bool(field.get("_answered")),
            }
        )
    if not records:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
    return len(records)


def remember_questions(
    store: Any | None,
    fields: list[dict[str, Any]],
    *,
    job_id: object = "",
    title: str = "",
    company: str = "",
) -> int:
    """Upsert every capturable field into SQLite. No-op when store is missing."""
    if store is None:
        return 0
    seen: set[tuple[str, str, str]] = set()
    stored = 0
    for field in fields:
        if should_skip_field(field):
            continue
        raw_question = _redact(field.get("question"))
        normalized = normalize_question(raw_question)
        if not normalized:
            continue
        kind = str(field.get("kind") or "text") or "text"
        options = [_redact(option) for option in (field.get("options") or [])][:50]
        identity = (normalized, kind, option_set_hash(kind, options))
        if identity in seen:
            continue
        seen.add(identity)
        source = classify_source(field)
        row = store.upsert_question(
            raw_question=raw_question,
            field_kind=kind,
            options=options,
            required=bool(field.get("required")),
            job_id=str(job_id or ""),
            company=_redact(company),
            title=_redact(title),
            source=source,
            proposed_value=proposed_value_for(field, source),
            provenance=source_provenance(source),
            confidence=source_confidence(source),
        )
        if row:
            stored += 1
    return stored
