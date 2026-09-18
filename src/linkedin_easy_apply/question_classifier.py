"""Classify and normalize captured Easy Apply questions before operator review.

Heuristics always run (no Ollama). Local Llama may clean wording later from the
dashboard. This module never invents answers, never fine-tunes, and never talks
to LinkedIn.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from linkedin_easy_apply.question_capture import (
    PLACEHOLDER_OPTIONS,
    normalize_question,
    option_set_hash,
)

log = logging.getLogger(__name__)

CLASSIFIED_KINDS = (
    "email",
    "tel",
    "number",
    "text",
    "select",
    "textarea",
    "yes_no",
    "file",
    "date",
    "unknown",
)
CLASSIFIED_KIND_SET = frozenset(CLASSIFIED_KINDS)
ANSWER_SHAPES = frozenset(CLASSIFIED_KINDS) | frozenset({"string"})
YES_NO_VALUES = frozenset({"yes", "no"})
CLASSIFY_MAX_PER_REQUEST = 8
CLASSIFY_TIMEOUT_SEC = 8
CLASSIFY_NUM_PREDICT = 120

_EMAIL_QUESTION_RE = re.compile(r"\b(e-?mails?|email addresses?)\b", re.IGNORECASE)
_PHONE_QUESTION_RE = re.compile(
    r"\b((mobile|cell|home|work)\s+)?(phone|telephone)(\s+(number|no|#))?|"
    r"\b(mobile|cell)\s+(number|phone)\b|"
    r"^(phone|mobile|cell|tel)$",
    re.IGNORECASE,
)
_CITY_QUESTION_RE = re.compile(r"\b(city|cities|town|location)\b", re.IGNORECASE)
_UI_PLACEHOLDER_RE = re.compile(
    r"^(select(\s+an\s+option)?|please\s+select|choose(\s+one)?)$",
    re.IGNORECASE,
)
_EMAIL_TOKEN_RE = re.compile(
    r"^(e-?mails?|email addresses?|\[?(redacted[-_])?email\]?)$",
    re.IGNORECASE,
)
_PHONE_TOKEN_RE = re.compile(
    r"^((mobile|cell|home|work)\s+)?(phone|telephone|tel)(\s+(numbers?|no|#))?|"
    r"^\[?(redacted[-_])?phone\]?$",
    re.IGNORECASE,
)
_YEARS_RE = re.compile(
    r"(how many years|\byears of\b|\byrs\b|\byears'? experience\b)",
    re.IGNORECASE,
)
_YEARS_OF_AGE_RE = re.compile(r"\b(years of age|at least \d+)\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"\b(resume|cv|curriculum vitae)\b", re.IGNORECASE)
_DATE_RE = re.compile(
    r"\b(start date|end date|available date|date of birth|birth ?date|"
    r"available to start)\b",
    re.IGNORECASE,
)
_YES_NO_HINT_RE = re.compile(
    r"\b(authoriz|authoris|sponsorship|sponsor|visa|relocat|willing to|"
    r"yes\s*/\s*no|yes or no)\b",
    re.IGNORECASE,
)
_ANSWER_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

CLASSIFY_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cleaned_question": {"type": "string"},
        "kind": {"type": "string"},
        "answer_shape": {"type": "string"},
        "duplicate_of_id": {"type": "integer"},
        "confidence": {"type": "number"},
    },
    "required": ["cleaned_question", "kind"],
}

CLASSIFY_PROMPT = (
    "Classify this LinkedIn Easy Apply form question. Do not invent or suggest "
    "an answer. Do not fill the field. Only correct the field kind and clean "
    "the wording.\n"
    "Return JSON only with keys: cleaned_question, kind, answer_shape, "
    "duplicate_of_id (optional integer), confidence (0 to 1).\n"
    "kind must be one of: email, tel, number, text, select, textarea, yes_no, "
    "file, date, unknown.\n"
    "cleaned_question is a short operator-facing prompt, not an answer value.\n"
    "If this is the same question as another id you are given, set "
    "duplicate_of_id; otherwise omit it.\n"
)


def collapse_repeated_phrases(text: str) -> str:
    """Turn 'Email address' x3 into one 'Email address'."""
    words = [part for part in str(text or "").split() if part]
    count = len(words)
    if count < 2:
        return " ".join(words)
    for size in range(1, count // 2 + 1):
        if count % size:
            continue
        chunk = words[:size]
        if chunk * (count // size) == words:
            return " ".join(chunk)
    return " ".join(words)


def clean_display_text(raw: str) -> str:
    text = collapse_repeated_phrases(" ".join(str(raw or "").split()))
    return text.strip(" \t:*")


def _is_email_token(option: str) -> bool:
    text = str(option or "").strip()
    if not text:
        return False
    if _EMAIL_TOKEN_RE.fullmatch(text) or _EMAIL_TOKEN_RE.fullmatch(normalize_question(text)):
        return True
    return bool(_ANSWER_EMAIL_RE.fullmatch(text) or _ANSWER_EMAIL_RE.search(text))


def _is_phone_token(option: str) -> bool:
    text = str(option or "").strip()
    if not text:
        return False
    return bool(
        _PHONE_TOKEN_RE.fullmatch(text) or _PHONE_TOKEN_RE.fullmatch(normalize_question(text))
    )


def _is_ui_placeholder(option: str) -> bool:
    text = normalize_question(option)
    if not text or text in PLACEHOLDER_OPTIONS:
        return True
    return bool(_UI_PLACEHOLDER_RE.fullmatch(text))


def _option_signal(options: list[str] | None) -> str:
    """Classify a LinkedIn option list: email, tel, placeholder, or real choices."""
    values = _options(None, options)
    if not values:
        return ""
    emailish = 0
    phoneish = 0
    placeholders = 0
    real = 0
    for option in values:
        if _is_email_token(option):
            emailish += 1
            continue
        if _is_phone_token(option):
            phoneish += 1
            continue
        if _is_ui_placeholder(option):
            placeholders += 1
            continue
        real += 1
    if emailish and real == 0:
        return "email"
    if phoneish and real == 0:
        return "tel"
    if real == 0 and (placeholders or emailish or phoneish):
        return "placeholder"
    if real == 1 and emailish:
        return "email"
    return ""


def _options(row: dict[str, Any] | None, extra: list[str] | None = None) -> list[str]:
    if extra is not None:
        values = extra
    elif row:
        values = list(row.get("options") or [])
    else:
        values = []
    return [str(option) for option in values if str(option).strip()]


def _option_labels(options: list[str]) -> set[str]:
    labels: set[str] = set()
    for option in options:
        if _is_ui_placeholder(option) or _is_email_token(option) or _is_phone_token(option):
            continue
        text = normalize_question(option)
        if text:
            labels.add(text)
    return labels


def is_yes_no_options(options: list[str] | None) -> bool:
    labels = _option_labels(options or [])
    if not labels:
        return False
    return labels <= YES_NO_VALUES and bool(labels & YES_NO_VALUES)


def _canonical_kind(kind: str) -> str:
    text = str(kind or "").strip().lower()
    if text in {"phone"}:
        return "tel"
    if text in {"radio", "checkbox"}:
        return "select"
    if text in CLASSIFIED_KIND_SET:
        return text
    return ""


def _answer_shape_for(kind: str, requested: str = "") -> str:
    shape = str(requested or "").strip().lower()
    if shape == "string":
        shape = "text"
    if kind == "email":
        if shape in {"", "email", "text", "string"}:
            return "text"
        return shape if shape in ANSWER_SHAPES else "text"
    if kind == "tel":
        return "tel" if shape in {"", "tel", "phone", "text"} else shape
    if kind == "yes_no":
        return "yes_no"
    if kind == "number":
        return "number"
    if kind == "file":
        return "file"
    if kind == "date":
        return "date"
    if kind == "textarea":
        return "textarea"
    if kind == "select":
        return "select"
    if shape in ANSWER_SHAPES:
        return "text" if shape == "email" else shape
    return "text"


def cluster_key_for(
    cleaned_question: str,
    kind: str,
    options: list[str] | None = None,
) -> str:
    kind_l = _canonical_kind(kind) or str(kind or "").lower()
    if kind_l == "email":
        return "email"
    if kind_l == "tel":
        return "tel"
    if kind_l == "file":
        return "file"
    key = normalize_question(cleaned_question)
    hashed = option_set_hash("select" if kind_l in {"select", "yes_no"} else kind_l, options)
    if hashed:
        return key + "|" + hashed
    return key


def heuristic_classify(
    raw_question: str,
    field_kind: str = "",
    options: list[str] | None = None,
) -> dict[str, Any]:
    """Fast, Ollama-free classification. Safe to run inside worker upsert."""
    raw = clean_display_text(raw_question)
    text = normalize_question(raw)
    captured = _canonical_kind(field_kind)
    option_list = _options(None, options)
    option_signal = _option_signal(option_list)
    yes_no_opts = is_yes_no_options(option_list)
    placeholder_select = option_signal == "placeholder" or (
        captured in {"select", "radio"} and not _option_labels(option_list)
    )

    if captured == "file" or _RESUME_RE.search(text):
        return {
            "cleaned_question": raw or "Resume",
            "kind": "file",
            "answer_shape": "file",
            "cluster_key": "file",
            "not_user_answerable": True,
            "confidence": 0.95,
            "source": "heuristic",
        }
    if captured == "email" or _EMAIL_QUESTION_RE.search(text) or option_signal == "email":
        return {
            "cleaned_question": "Email",
            "kind": "email",
            "answer_shape": "text",
            "cluster_key": "email",
            "not_user_answerable": False,
            "confidence": 0.95,
            "source": "heuristic",
        }
    if captured == "tel" or _PHONE_QUESTION_RE.search(text) or option_signal == "tel":
        return {
            "cleaned_question": "Phone",
            "kind": "tel",
            "answer_shape": "tel",
            "cluster_key": "tel",
            "not_user_answerable": False,
            "confidence": 0.9,
            "source": "heuristic",
        }
    if yes_no_opts or _YES_NO_HINT_RE.search(text) or _YEARS_OF_AGE_RE.search(text):
        return {
            "cleaned_question": raw,
            "kind": "yes_no",
            "answer_shape": "yes_no",
            "cluster_key": cluster_key_for(raw, "yes_no", option_list),
            "not_user_answerable": False,
            "confidence": 0.85 if yes_no_opts else 0.75,
            "source": "heuristic",
        }
    if captured == "number" or (_YEARS_RE.search(text) and not _YEARS_OF_AGE_RE.search(text)):
        return {
            "cleaned_question": raw,
            "kind": "number",
            "answer_shape": "number",
            "cluster_key": cluster_key_for(raw, "number", option_list),
            "not_user_answerable": False,
            "confidence": 0.9,
            "source": "heuristic",
        }
    if captured == "date" or _DATE_RE.search(text):
        return {
            "cleaned_question": raw,
            "kind": "date",
            "answer_shape": "date",
            "cluster_key": cluster_key_for(raw, "date", option_list),
            "not_user_answerable": False,
            "confidence": 0.8,
            "source": "heuristic",
        }
    if captured == "textarea":
        return {
            "cleaned_question": raw,
            "kind": "textarea",
            "answer_shape": "textarea",
            "cluster_key": cluster_key_for(raw, "textarea", option_list),
            "not_user_answerable": False,
            "confidence": 0.7,
            "source": "heuristic",
        }
    if _CITY_QUESTION_RE.search(text) and (
        placeholder_select or option_signal in {"email", "placeholder"}
    ):
        return {
            "cleaned_question": raw or "City",
            "kind": "text",
            "answer_shape": "text",
            "cluster_key": cluster_key_for(raw or "City", "text", []),
            "not_user_answerable": False,
            "confidence": 0.85,
            "source": "heuristic",
        }
    if (captured == "select" or option_list) and not placeholder_select:
        kind = "yes_no" if yes_no_opts else "select"
        return {
            "cleaned_question": raw,
            "kind": kind,
            "answer_shape": _answer_shape_for(kind),
            "cluster_key": cluster_key_for(raw, kind, option_list),
            "not_user_answerable": False,
            "confidence": 0.7,
            "source": "heuristic",
        }
    kind = captured if captured in CLASSIFIED_KIND_SET else "text"
    if not kind or kind == "select":
        kind = "text"
    return {
        "cleaned_question": raw,
        "kind": kind,
        "answer_shape": _answer_shape_for(kind),
        "cluster_key": cluster_key_for(raw, kind, option_list if kind == "select" else []),
        "not_user_answerable": False,
        "confidence": 0.55,
        "source": "heuristic",
    }


def _looks_like_invented_answer(cleaned: str, raw: str) -> bool:
    text = clean_display_text(cleaned)
    if not text:
        return True
    lowered = text.casefold()
    if lowered in YES_NO_VALUES:
        return True
    if _ANSWER_EMAIL_RE.search(text):
        return True
    return bool(
        re.fullmatch(r"-?\d+(?:\.\d+)?", text)
        and not re.fullmatch(r"-?\d+(?:\.\d+)?", clean_display_text(raw))
    )


def _parse_llm_classification(
    payload: dict[str, Any],
    row: dict[str, Any],
    heuristic: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    kind = _canonical_kind(str(payload.get("kind") or ""))
    heuristic_kind = _canonical_kind(str(heuristic.get("kind") or ""))
    if heuristic_kind in {"email", "tel"}:
        kind = heuristic_kind
    elif heuristic_kind == "text" and kind in {"select", "radio", ""}:
        kind = "text"
    if kind not in CLASSIFIED_KIND_SET:
        kind = str(heuristic.get("kind") or "text")
    cleaned = clean_display_text(str(payload.get("cleaned_question") or ""))
    raw = str(row.get("raw_question") or row.get("question_text") or "")
    if heuristic_kind in {"email", "tel"}:
        cleaned = str(heuristic.get("cleaned_question") or cleaned)
    if not cleaned or _looks_like_invented_answer(cleaned, raw):
        cleaned = str(heuristic.get("cleaned_question") or raw)
    shape = _answer_shape_for(kind, str(payload.get("answer_shape") or ""))
    confidence = heuristic.get("confidence")
    try:
        raw_conf = float(payload.get("confidence"))
        if 0.0 <= raw_conf <= 1.0:
            confidence = raw_conf
    except (TypeError, ValueError):
        pass
    duplicate_of_id = payload.get("duplicate_of_id")
    try:
        dup = int(duplicate_of_id) if duplicate_of_id not in (None, "") else None
    except (TypeError, ValueError):
        dup = None
    row_id = row.get("id")
    if dup is not None and (dup <= 0 or str(dup) == str(row_id or "")):
        dup = None
    not_user_answerable = kind == "file" or bool(heuristic.get("not_user_answerable"))
    return {
        "cleaned_question": cleaned,
        "kind": kind,
        "answer_shape": shape,
        "cluster_key": cluster_key_for(cleaned, kind, _options(row)),
        "not_user_answerable": not_user_answerable,
        "confidence": confidence,
        "duplicate_of_id": dup,
        "source": "llm",
    }


def _llm_classify(
    row: dict[str, Any],
    *,
    timeout: int,
    config_module: Any | None,
) -> tuple[dict[str, Any], bool]:
    from linkedin_easy_apply.llm import generate_json
    from linkedin_easy_apply.llm import status as llm_status

    info = llm_status(config_module)
    if not info.get("ready"):
        return {}, False
    payload = {
        "raw_question": str(row.get("raw_question") or row.get("question_text") or ""),
        "options": _options(row)[:20],
        "current_kind": str(
            row.get("classified_kind") or row.get("field_kind") or row.get("kind") or ""
        ),
        "question_id": row.get("id"),
    }
    prompt = CLASSIFY_PROMPT + "\n" + json.dumps(payload, ensure_ascii=True)
    try:
        return (
            generate_json(
                prompt,
                CLASSIFY_RESPONSE_FORMAT,
                config_module=config_module,
                timeout=int(timeout),
                num_predict=CLASSIFY_NUM_PREDICT,
            ),
            True,
        )
    except (OSError, TimeoutError, TypeError, ValueError) as exc:
        log.warning(
            "question llm classify skipped id=%s err=%s",
            row.get("id"),
            exc.__class__.__name__,
        )
        return {}, True


def _persist_classification(store: Any, row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    save = getattr(store, "save_question_classification", None)
    if not callable(save):
        return {**row, **result}
    saved = save(
        row.get("id"),
        cleaned_question=str(result.get("cleaned_question") or ""),
        classified_kind=str(result.get("kind") or ""),
        answer_shape=str(result.get("answer_shape") or ""),
        cluster_key=str(result.get("cluster_key") or ""),
        classification_status=str(
            result.get("classification_status") or result.get("source") or "heuristic"
        ),
        not_user_answerable=bool(result.get("not_user_answerable")),
    )
    return saved or {**row, **result}


def _maybe_merge(store: Any, row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    merge = getattr(store, "merge_duplicate_question", None)
    if not callable(merge):
        return row
    duplicate_of = result.get("duplicate_of_id")
    cluster = str(result.get("cluster_key") or "")
    merged = merge(row.get("id"), cluster_key=cluster, duplicate_of_id=duplicate_of)
    return merged or row


def _heuristic_for_row(row: dict[str, Any]) -> dict[str, Any]:
    return heuristic_classify(
        str(row.get("raw_question") or row.get("question_text") or ""),
        str(row.get("field_kind") or row.get("kind") or ""),
        _options(row),
    )


def classification_needs_repair(row: dict[str, Any] | None) -> bool:
    """True when a stored select/text row should be email, phone, or plain text."""
    if not row:
        return False
    if str(row.get("merged_into_id") or "").strip() not in {"", "0", "None"}:
        return False
    if str(row.get("classification_status") or "").strip().lower() == "duplicate":
        return False
    result = _heuristic_for_row(row)
    wanted = _canonical_kind(str(result.get("kind") or ""))
    stored = _canonical_kind(
        str(row.get("classified_kind") or row.get("field_kind") or row.get("kind") or "")
    )
    cleaned = str(row.get("cleaned_question") or "").strip()
    if wanted in {"email", "tel"} and stored != wanted:
        return True
    if wanted == "email" and cleaned.casefold() != "email":
        return True
    if wanted == "tel" and cleaned.casefold() != "phone":
        return True
    return stored in {"select", "radio"} and wanted in {"email", "tel", "text", "number"}


def repair_contact_classifications(store: Any, *, limit: int = 300) -> int:
    """Reclassify stored email/phone placeholder-selects and merge email clusters."""
    list_fn = getattr(store, "list_questions", None)
    if not callable(list_fn):
        return 0
    repaired = 0
    try:
        rows = list_fn(limit=int(limit), include_hidden=False)
    except (OSError, TypeError, ValueError):
        return 0
    for row in rows:
        if not classification_needs_repair(row):
            continue
        result = _heuristic_for_row(row)
        try:
            saved = _persist_classification(store, row, result)
            _maybe_merge(store, saved, result)
            repaired += 1
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            log.warning(
                "contact classify repair skipped id=%s err=%s",
                row.get("id"),
                exc.__class__.__name__,
            )
    return repaired


def apply_fast_classification(store: Any, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Heuristic-only hook after upsert. Must stay well under 200ms."""
    if not row:
        return row
    status = str(row.get("classification_status") or "").strip().lower()
    if (
        status in {"heuristic", "llm", "duplicate"}
        and str(row.get("classified_kind") or "").strip()
        and not classification_needs_repair(row)
    ):
        return row
    try:
        result = heuristic_classify(
            str(row.get("raw_question") or row.get("question_text") or ""),
            str(row.get("field_kind") or row.get("kind") or ""),
            _options(row),
        )
        saved = _persist_classification(store, row, result)
        return _maybe_merge(store, saved, result)
    except (OSError, TypeError, ValueError) as exc:
        log.warning("heuristic classify skipped id=%s err=%s", row.get("id"), exc.__class__.__name__)
        return row


def classify_question_with_llm(
    row: dict[str, Any],
    store: Any | None = None,
    *,
    use_llm: bool = True,
    timeout: int = CLASSIFY_TIMEOUT_SEC,
    config_module: Any | None = None,
) -> dict[str, Any]:
    """Background-safe classify+normalize. Heuristic first; Ollama optional.

    Never invents answers. Never starts Easy Apply. Safe if Ollama is down.
    """
    heuristic = heuristic_classify(
        str(row.get("raw_question") or row.get("question_text") or ""),
        str(row.get("field_kind") or row.get("kind") or ""),
        _options(row),
    )
    result = dict(heuristic)
    attempted_llm = False
    if use_llm:
        payload, attempted_llm = _llm_classify(
            row, timeout=timeout, config_module=config_module
        )
        parsed = _parse_llm_classification(payload, row, heuristic)
        if parsed:
            result = parsed
        elif attempted_llm:
            result["classification_status"] = "llm"
    if store is not None:
        saved = _persist_classification(store, row, result)
        merged = _maybe_merge(store, saved, result)
        result = {
            **result,
            "id": merged.get("id"),
            "cleaned_question": merged.get("cleaned_question") or result.get("cleaned_question"),
            "classified_kind": merged.get("classified_kind") or result.get("kind"),
            "answer_shape": merged.get("answer_shape") or result.get("answer_shape"),
            "merged_into_id": merged.get("merged_into_id"),
            "seen_count": merged.get("seen_count"),
        }
    return result


def classify_pending_questions(
    store: Any,
    *,
    max_n: int = CLASSIFY_MAX_PER_REQUEST,
    timeout: int = CLASSIFY_TIMEOUT_SEC,
    config_module: Any | None = None,
) -> dict[str, Any]:
    """Dashboard batch: heuristic leftovers plus optional Ollama polish. Caps work."""
    from linkedin_easy_apply.llm import status as llm_status

    cap = max(0, int(max_n))
    try:
        repair_contact_classifications(store)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        log.warning("contact classify repair skipped err=%s", exc.__class__.__name__)
    ready = False
    try:
        ready = bool(llm_status(config_module).get("ready"))
    except (OSError, TimeoutError, TypeError, ValueError):
        ready = False
    list_fn = getattr(store, "list_questions_needing_classification", None)
    rows = list_fn(limit=cap, include_heuristic=ready) if callable(list_fn) else []
    classified = 0
    llm_used = 0
    for row in rows:
        result = classify_question_with_llm(
            row,
            store,
            use_llm=ready,
            timeout=timeout,
            config_module=config_module,
        )
        classified += 1
        if str(result.get("source") or "") == "llm":
            llm_used += 1
    remaining_fn = getattr(store, "count_questions_needing_classification", None)
    remaining = 0
    if callable(remaining_fn):
        remaining = int(remaining_fn(include_heuristic=ready) or 0)
    return {
        "ok": True,
        "classified": classified,
        "llm_used": llm_used,
        "remaining": remaining,
        "ollama_ready": ready,
    }
