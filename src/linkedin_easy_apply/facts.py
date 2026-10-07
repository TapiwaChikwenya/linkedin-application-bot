"""Applicant facts the local LLM is allowed to use. Nothing is invented here."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def extract_resume_text(path: str | None = None, limit: int = 8000) -> str:
    """Best-effort PDF text. Empty when the file is missing or pypdf is unavailable."""
    from linkedin_easy_apply.operator_settings import resolved_resume_path

    target = str(path or "").strip() or resolved_resume_path()
    if not target or not os.path.isfile(target):
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(target)
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
            if sum(len(part) for part in chunks) >= limit:
                break
        return " ".join(" ".join(chunks).split())[:limit]
    except Exception:  # noqa: BLE001 - pypdf raises several parse errors
        return ""


APPROVED_FACTS_LIMIT = 12
CATCHALL_YEAR_KEYS = frozenset({"default"})
CATCHALL_YES_NO_RULES = frozenset({("experience",), ("years", "experience")})


def mapped_years_experience(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Skill-year bootstrap map without the generic `default` catch-all."""
    cleaned: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        skill = " ".join(str(key or "").split())
        if not skill or skill.casefold() in CATCHALL_YEAR_KEYS:
            continue
        cleaned[skill] = value
    return cleaned


def mapped_yes_no_rules(raw: Any | None) -> list[tuple[tuple[str, ...], str]]:
    """Keyword Yes/No bootstrap rules without generic experience catch-alls."""
    cleaned: list[tuple[tuple[str, ...], str]] = []
    for item in raw or []:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        keywords, answer = item
        if not isinstance(keywords, (tuple, list)):
            continue
        key = tuple(str(word).casefold() for word in keywords)
        if not key or key in CATCHALL_YES_NO_RULES:
            continue
        cleaned.append((tuple(str(word) for word in keywords), str(answer)))
    return cleaned


def compact_approved_answers(
    store: Any | None = None,
    questions: list[dict[str, Any]] | None = None,
    limit: int = APPROVED_FACTS_LIMIT,
) -> list[dict[str, str]]:
    """Small reviewed Q&A slice for the prompt. Never the whole SQLite history."""
    if store is None:
        return []
    try:
        if questions:
            retrieve = getattr(store, "retrieve_approved_answers", None)
            rows = list(retrieve(questions, limit=limit) or []) if callable(retrieve) else []
        else:
            listed = getattr(store, "list_approved_questions", None)
            rows = list(listed(limit=limit) or []) if callable(listed) else []
    except (OSError, TypeError, ValueError):
        return []
    compact: list[dict[str, str]] = []
    for row in rows[:limit]:
        value = str(row.get("value") or row.get("approved_value") or "").strip()
        question = str(
            row.get("question")
            or row.get("raw_question")
            or row.get("question_text")
            or row.get("normalized_question")
            or ""
        ).strip()
        source_id = str(row.get("source_id") or row.get("id") or row.get("question_id") or "")
        if not value or not question:
            continue
        compact.append({"source_id": source_id, "question": question, "value": value})
    return compact


def applicant_facts(
    config_module: Any | None = None,
    store: Any | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured, local-only facts sent to Ollama with a form snapshot."""
    import config as default_config
    from linkedin_easy_apply.operator_settings import (
        resolved_applicant_summary,
        resolved_resume_path,
    )

    cfg = config_module or default_config
    resume_path = resolved_resume_path(store=store, config_module=cfg)
    resume_name = Path(resume_path).name if resume_path else ""
    years = mapped_years_experience(getattr(cfg, "years_experience", {}) or {})
    yes_no = [
        {"when_all_keywords": list(keywords), "answer": answer}
        for keywords, answer in mapped_yes_no_rules(getattr(cfg, "yes_no_answers", []))
    ]
    return {
        "first_name": str(getattr(cfg, "FirstName", "") or ""),
        "last_name": str(getattr(cfg, "LastName", "") or ""),
        "email": str(getattr(cfg, "email", "") or getattr(cfg, "Email", "") or ""),
        "phone": str(getattr(cfg, "phone_number", "") or ""),
        "city": str(getattr(cfg, "application_city", "") or ""),
        "linkedin_url": str(getattr(cfg, "LinkedInProfileURL", "") or ""),
        "country_code": str(getattr(cfg, "country_code", "") or ""),
        "summary": resolved_applicant_summary(store=store),
        "resume_filename": resume_name,
        "resume_text": extract_resume_text(resume_path),
        "years_experience": years,
        "yes_no_rules": yes_no,
        "work_authorization_us": "Yes",
        "requires_sponsorship": "No",
        "approved_answers": compact_approved_answers(store, questions),
    }
