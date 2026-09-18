"""Local Llama job-fit gate. Retrieve-then-score, never fine-tune."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

FIT_THRESHOLD = 0.55
MIN_SNIPPET_CHARS = 80
SNIPPET_LIMIT = 1200
SKILL_LIMIT = 16
APPROVED_FACTS_LIMIT = 8
FIT_TIMEOUT_SEC = 25
FIT_NUM_PREDICT = 160

FIT_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fit": {"type": "number"},
        "reason": {"type": "string"},
        "decision": {"type": "string", "enum": ["skip", "apply"]},
    },
    "required": ["fit", "reason", "decision"],
}

FIT_LOG_RE = re.compile(
    r"Job fit llama=([0-9]+(?:\.[0-9]+)?)\s+(apply|skip)(?::\s*(.*))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobFitResult:
    """Outcome of one listing score. `skip_job` is the worker action."""

    ready: bool
    fit: float | None
    decision: str
    reason: str
    source: str
    skip_job: bool
    needs_description: bool = False
    threshold: float = FIT_THRESHOLD

    def log_line(self) -> str:
        if self.needs_description and not self.ready:
            return "Job fit llama deferred: snippet too short"
        if not self.ready:
            detail = self.reason or "Ollama not ready"
            return "Job fit llama skipped: " + detail
        score = 0.0 if self.fit is None else self.fit
        if self.skip_job:
            extra = compact_text(self.reason, 80)
            suffix = ": " + extra if extra else ""
            return f"Job fit llama={score:.2f} skip{suffix}"
        return f"Job fit llama={score:.2f} apply"

    def as_status(self) -> dict[str, Any]:
        return {
            "score": self.fit,
            "decision": "skip" if self.skip_job else "apply",
            "reason": self.reason,
            "source": self.source,
            "ready": self.ready,
            "line": self.log_line(),
            "needs_description": self.needs_description,
        }


def compact_text(value: str, limit: int = SNIPPET_LIMIT) -> str:
    return " ".join(str(value or "").split())[:limit]


def snippet_is_usable(snippet: str, minimum: int = MIN_SNIPPET_CHARS) -> bool:
    return len(compact_text(snippet)) >= minimum


def fit_threshold(config_module: Any | None = None) -> float:
    import config as default_config

    cfg = config_module or default_config
    raw = getattr(cfg, "job_fit_threshold", FIT_THRESHOLD)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return FIT_THRESHOLD
    return min(1.0, max(0.0, value))


def compact_fit_profile(
    config_module: Any | None = None,
    store: Any | None = None,
) -> dict[str, Any]:
    """Keywords, skill keys, and approved facts only. No resume dump, no invented skills."""
    import config as default_config
    from linkedin_easy_apply.facts import compact_approved_answers, mapped_years_experience

    cfg = config_module or default_config
    years = mapped_years_experience(getattr(cfg, "years_experience", {}) or {})
    skills = [str(skill) for skill in years if str(skill).strip()][:SKILL_LIMIT]
    keywords = [
        " ".join(str(item).split())
        for item in (getattr(cfg, "keywords", None) or [])
        if str(item).strip()
    ]
    skill_years = {skill: years[skill] for skill in skills if skill in years}
    approved = compact_approved_answers(store, questions=None, limit=APPROVED_FACTS_LIMIT)
    summary = compact_text(
        str(getattr(cfg, "applicant_summary", "") or "")
        or os.getenv("LINKEDIN_APPLICANT_SUMMARY", ""),
        400,
    )
    return {
        "keywords": keywords,
        "skills": skills,
        "skill_years": skill_years,
        "approved_facts": approved,
        "summary": summary,
    }


def build_fit_prompt(
    title: str,
    company: str,
    snippet: str,
    profile: dict[str, Any],
) -> str:
    payload = {
        "job": {
            "title": compact_text(title, 200),
            "company": compact_text(company, 200),
            "snippet": compact_text(snippet, SNIPPET_LIMIT),
        },
        "applicant_profile": profile,
    }
    return (
        "You score whether this LinkedIn job is a fit for the applicant.\n"
        "Use only applicant_profile.keywords, skills, skill_years, approved_facts, "
        "summary, and the job title/company/snippet.\n"
        "Do not invent experience, employers, degrees, clearances, or skills that are "
        "not in applicant_profile.\n"
        "If the posting is staffing or recruiter spam, intern, unpaid, or clearly "
        "unrelated to the keywords and skills, decision is skip.\n"
        "fit is a number from 0 to 1 grounded only in those keywords and skills.\n"
        "decision is exactly apply or skip.\n"
        "reason is a short phrase under 80 characters.\n"
        "Return JSON only with this shape:\n"
        '{"fit":0.0,"reason":"...","decision":"apply"}\n\n'
        + json.dumps(payload, ensure_ascii=True)
    )


def normalize_fit_payload(
    payload: dict[str, Any],
    *,
    threshold: float = FIT_THRESHOLD,
    source: str = "description",
    ready: bool = True,
) -> JobFitResult:
    if not isinstance(payload, dict) or not payload:
        return JobFitResult(
            ready=False,
            fit=None,
            decision="apply",
            reason="invalid fit payload",
            source=source,
            skip_job=False,
            threshold=threshold,
        )
    try:
        fit = float(payload.get("fit"))
    except (TypeError, ValueError):
        return JobFitResult(
            ready=False,
            fit=None,
            decision="apply",
            reason="invalid fit score",
            source=source,
            skip_job=False,
            threshold=threshold,
        )
    fit = min(1.0, max(0.0, fit))
    raw_decision = str(payload.get("decision") or "").strip().lower()
    if raw_decision not in {"skip", "apply"}:
        raw_decision = "skip" if fit < threshold else "apply"
    reason = compact_text(str(payload.get("reason") or ""), 80)
    skip_job = raw_decision == "skip" or fit < threshold
    decision = "skip" if skip_job else "apply"
    return JobFitResult(
        ready=ready,
        fit=fit,
        decision=decision,
        reason=reason,
        source=source,
        skip_job=skip_job,
        threshold=threshold,
    )


def evaluate_listing_fit(
    *,
    title: str,
    company: str,
    snippet: str = "",
    source: str = "card",
    store: Any | None = None,
    config_module: Any | None = None,
    generate_json: Any | None = None,
) -> JobFitResult:
    """Score a search card or job-page excerpt. Short card snippets defer instead of guessing.

    If Ollama is down or returns garbage, `skip_job` is False so the run continues.
    """
    threshold = fit_threshold(config_module)
    compact_snippet = compact_text(snippet, SNIPPET_LIMIT)
    if source == "card" and not snippet_is_usable(compact_snippet):
        return JobFitResult(
            ready=False,
            fit=None,
            decision="apply",
            reason="snippet too short",
            source=source,
            skip_job=False,
            needs_description=True,
            threshold=threshold,
        )
    from linkedin_easy_apply.llm import generate_json as default_generate_json

    producer = generate_json or default_generate_json
    profile = compact_fit_profile(config_module, store)
    prompt = build_fit_prompt(title, company, compact_snippet, profile)
    payload = producer(
        prompt,
        FIT_RESPONSE_FORMAT,
        config_module=config_module,
        timeout=FIT_TIMEOUT_SEC,
        num_predict=FIT_NUM_PREDICT,
    )
    if not payload:
        return JobFitResult(
            ready=False,
            fit=None,
            decision="apply",
            reason="Ollama not ready",
            source=source,
            skip_job=False,
            threshold=threshold,
        )
    return normalize_fit_payload(
        payload,
        threshold=threshold,
        source=source,
        ready=True,
    )


def parse_fit_log(message: str) -> dict[str, Any] | None:
    match = FIT_LOG_RE.search(str(message or "").strip())
    if not match:
        return None
    score = float(match.group(1))
    decision = match.group(2).lower()
    reason = compact_text(match.group(3) or "")
    return {
        "score": score,
        "decision": decision,
        "reason": reason,
        "line": compact_text(message, 200),
        "ready": True,
    }


def last_fit_status(
    events: list[dict[str, Any]] | None = None,
    job: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compact status field for Live. Prefer the latest fit log event, else the job row."""
    for event in events or []:
        parsed = parse_fit_log(str(event.get("message") or ""))
        if parsed:
            parsed["job_id"] = str(event.get("job_id") or "")
            return parsed
    if not job:
        return None
    score = job.get("fit_score")
    status = str(job.get("status") or "")
    reason = compact_text(str(job.get("reason") or ""), 120)
    if score is None and status != "skipped_fit":
        return None
    try:
        fit = float(score) if score is not None else None
    except (TypeError, ValueError):
        fit = None
    decision = "skip" if status == "skipped_fit" else "apply"
    if fit is None:
        line = reason or "Job fit llama skipped"
    elif decision == "skip":
        extra = reason
        if extra.lower().startswith("job fit llama="):
            line = extra
        else:
            line = f"Job fit llama={fit:.2f} skip" + (": " + extra if extra else "")
    else:
        line = f"Job fit llama={fit:.2f} apply"
    return {
        "score": fit,
        "decision": decision,
        "reason": reason,
        "job_id": str(job.get("job_id") or ""),
        "line": line,
        "ready": fit is not None,
    }
