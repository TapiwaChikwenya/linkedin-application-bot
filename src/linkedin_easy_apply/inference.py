"""Grounded inference and interpolation for leftover Easy Apply fields.

Never invents legal, demographic, salary, clearance, or sponsorship facts.
Related skill years and same-family Yes/No answers may be interpolated from
`years_experience`, approved SQLite rows, and resume-backed LLM summaries.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from linkedin_easy_apply.question_capture import normalize_question

# Longer aliases must stay before shorter ones inside a family so lookups prefer
# the named skill, then the nearest related tool (Databricks → PySpark → Spark).
RELATED_SKILL_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("databricks", "pyspark", "spark", "apache spark"),
    ("azure data factory", "data factory", "adf"),
    ("sql server", "t-sql", "tsql", "sql"),
    ("postgresql", "postgres"),
    ("power bi", "powerbi"),
    ("ssis", "sql server integration services"),
    ("ssrs", "sql server reporting services"),
    ("kubernetes", "k8s"),
)

_YEARS_QUESTION_RE = re.compile(
    r"(how many years|\byears of\b|\byrs\b|\byears'? experience\b|\byear(?:s)?\b)",
    re.IGNORECASE,
)
_YEARS_OF_AGE_RE = re.compile(r"\b(years of age|at least \d+)\b", re.IGNORECASE)
_INTEGER_YEARS_RE = re.compile(
    r"^(-?\d+)(?:\.0+)?(?:\s*(?:years?|yrs?))?$",
    re.IGNORECASE,
)

SPONSORSHIP_FAMILY = "sponsorship"
WORK_AUTH_FAMILY = "work_auth"
_YES_NO_FAMILY_TOKENS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        SPONSORSHIP_FAMILY,
        frozenset({"sponsor", "sponsored", "sponsorship"}),
    ),
    (
        WORK_AUTH_FAMILY,
        frozenset({"authorization", "authorized", "authorised"}),
    ),
    ("clearance", frozenset({"clearance"})),
    ("relocate", frozenset({"relocate", "relocation"})),
    ("remote", frozenset({"remote"})),
    ("hybrid", frozenset({"hybrid"})),
    ("onsite", frozenset({"onsite", "on-site"})),
    ("commute", frozenset({"commute"})),
    ("travel", frozenset({"travel"})),
    ("background_check", frozenset({"background"})),
    ("drug_screen", frozenset({"drug"})),
    ("license", frozenset({"license", "licence", "driver", "drivers"})),
)


@dataclass(frozen=True)
class InferredAnswer:
    value: str
    note: str = ""
    interpolated: bool = False
    source_skill: str = ""


def integer_years(value: Any) -> str:
    """Return a whole-year string, or empty when the value is not a clean integer."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return str(int(value))
    text = " ".join(str(value).split())
    if not text:
        return ""
    match = _INTEGER_YEARS_RE.fullmatch(text)
    if not match:
        return ""
    return str(int(match.group(1)))


def is_years_question(text: str) -> bool:
    blob = " ".join(str(text or "").split())
    if not blob or _YEARS_OF_AGE_RE.search(blob):
        return False
    return bool(_YEARS_QUESTION_RE.search(blob))


def skill_catalog(extra: Any | None = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for family in RELATED_SKILL_FAMILIES:
        for item in family:
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(item)
    for item in extra or []:
        text = " ".join(str(item or "").split())
        key = text.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(text)
    names.sort(key=len, reverse=True)
    return names


def named_skills_in_question(question: str, extra: Any | None = None) -> list[str]:
    blob = normalize_question(question)
    if not blob:
        return []
    found: list[str] = []
    for skill in skill_catalog(extra):
        needle = skill.casefold()
        if needle and needle in blob:
            found.append(skill)
    return found


def related_skill_family(skill: str) -> tuple[str, ...]:
    key = " ".join(str(skill or "").split()).casefold()
    if not key:
        return ()
    for family in RELATED_SKILL_FAMILIES:
        folded = tuple(item.casefold() for item in family)
        if key in folded:
            return family
    return (skill,)


def yes_no_family(text: str) -> str:
    """Return a Yes/No interpolation family. Sponsorship never shares work auth."""
    from linkedin_easy_apply.store import question_tokens

    tokens = question_tokens(text)
    if not tokens:
        return ""
    for family, needles in _YES_NO_FAMILY_TOKENS:
        if tokens & needles:
            return family
    return ""


def families_may_interpolate(left: str, right: str) -> bool:
    return bool(left) and left == right


def lookup_skill_years(
    skill: str,
    years_map: dict[str, Any] | None,
) -> tuple[str, str, bool]:
    """Return (years, source_skill, interpolated) for a named skill."""
    folded: dict[str, tuple[str, Any]] = {}
    for key, value in (years_map or {}).items():
        name = " ".join(str(key or "").split())
        if not name or name.casefold() == "default":
            continue
        folded[name.casefold()] = (name, value)
    wanted = " ".join(str(skill or "").split())
    if not wanted:
        return "", "", False
    exact = folded.get(wanted.casefold())
    if exact is not None:
        years = integer_years(exact[1])
        if years:
            return years, exact[0], False
    for member in related_skill_family(wanted):
        if member.casefold() == wanted.casefold():
            continue
        related = folded.get(member.casefold())
        if related is None:
            continue
        years = integer_years(related[1])
        if years:
            return years, related[0], True
    return "", "", False


def skill_years_for_question(
    question: str,
    years_map: dict[str, Any] | None,
    *,
    kind: str = "",
) -> InferredAnswer | None:
    """Specific mapped skill first, then nearest related family member."""
    kind_l = str(kind or "").lower()
    if kind_l and kind_l != "number" and not is_years_question(question):
        return None
    named = named_skills_in_question(question, (years_map or {}).keys())
    if not named:
        return None
    years, source, interpolated = lookup_skill_years(named[0], years_map)
    if not years:
        return None
    note = ""
    if interpolated:
        note = f"Interpolated years {years} from {source}"
    return InferredAnswer(
        value=years,
        note=note,
        interpolated=interpolated,
        source_skill=source,
    )


def _approved_question_text(row: dict[str, Any]) -> str:
    return str(
        row.get("normalized_question")
        or row.get("raw_question")
        or row.get("question")
        or row.get("question_text")
        or ""
    )


def _approved_value(row: dict[str, Any]) -> str:
    return str(row.get("approved_value") or row.get("value") or "").strip()


def interpolate_approved_for_question(
    approved: list[dict[str, Any]],
    question: dict[str, Any] | str,
    used: set[str],
) -> dict[str, Any] | None:
    """Related approved years or same-family Yes/No. Never sponsorship ← work auth."""
    from linkedin_easy_apply.store import compact_approved_row

    if isinstance(question, dict):
        qtext = str(question.get("question") or "")
        kind = str(question.get("kind") or "").lower()
    else:
        qtext = str(question or "")
        kind = ""
    if not qtext.strip():
        return None

    pool = [
        row
        for row in approved
        if _approved_value(row)
    ]
    catalog = skill_catalog()
    query_skill = ""
    if is_years_question(qtext) or kind == "number":
        named = named_skills_in_question(qtext, catalog)
        query_skill = named[0] if named else ""
    if query_skill:
        family = related_skill_family(query_skill)
        family_folded = [item.casefold() for item in family]
        best: dict[str, str] | None = None
        best_rank = len(family_folded) + 1
        for row in pool:
            ident = str(row.get("id") or row.get("question_id") or row.get("source_id") or "")
            if ident and ident in used:
                continue
            stored_named = named_skills_in_question(_approved_question_text(row), catalog)
            if not stored_named:
                continue
            source = stored_named[0]
            try:
                rank = family_folded.index(source.casefold())
            except ValueError:
                continue
            years = integer_years(_approved_value(row))
            if not years:
                continue
            interpolated = source.casefold() != query_skill.casefold()
            note = f"Interpolated years {years} from {source}" if interpolated else ""
            payload = compact_approved_row(
                row,
                match="interpolated" if interpolated else "overlap",
                note=note,
            )
            if not (payload.get("source_id") and payload.get("question") and payload.get("value")):
                continue
            payload["value"] = years
            if note:
                payload["match"] = "interpolated"
            if rank < best_rank:
                best = payload
                best_rank = rank
        if best is not None:
            return best

    family = yes_no_family(qtext)
    if not family:
        return None
    for row in pool:
        ident = str(row.get("id") or row.get("question_id") or row.get("source_id") or "")
        if ident and ident in used:
            continue
        stored_family = yes_no_family(_approved_question_text(row))
        if not families_may_interpolate(family, stored_family):
            continue
        raw = _approved_value(row)
        cleaned = raw.strip()
        if cleaned.casefold() not in {"yes", "no"}:
            continue
        payload = compact_approved_row(row, match="interpolated")
        if not (payload.get("source_id") and payload.get("question") and payload.get("value")):
            continue
        payload["value"] = "Yes" if cleaned.casefold() == "yes" else "No"
        payload["note"] = "Inferred from related approved answer"
        return payload
    return None


def leftover_required_non_sensitive(
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from linkedin_easy_apply.store import is_sensitive_question

    filled = {
        normalize_question(str(item.get("question") or ""))
        for item in answers
        if str(item.get("question") or "").strip() and str(item.get("value") or "").strip()
    }
    leftover: list[dict[str, Any]] = []
    for question in questions:
        text = str(question.get("question") or "")
        key = normalize_question(text)
        if not key or key in filled:
            continue
        if not question.get("required"):
            continue
        if is_sensitive_question(text):
            continue
        leftover.append(question)
    return leftover


def conservative_inferred_value(
    field: dict[str, Any],
    years_map: dict[str, Any] | None,
) -> InferredAnswer | None:
    """Last grounded fallback. Named mapped skill only; never a global default."""
    from linkedin_easy_apply.store import is_sensitive_question

    question = str(field.get("question") or "")
    if not question.strip() or is_sensitive_question(question):
        return None
    return skill_years_for_question(question, years_map, kind=str(field.get("kind") or ""))


def log_question_label(question: str, limit: int = 180) -> str:
    """Operator-facing field label. Never a resume body."""
    return " ".join(str(question or "").split())[:limit]
