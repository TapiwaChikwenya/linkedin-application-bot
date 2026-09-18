"""Safe, collision-resistant diagnostic artifact helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone


def artifact_stem(kind: str, job_id: object, now: datetime | None = None) -> str:
    """Return a sortable per-job artifact stem that will not overwrite prior failures."""
    instant = now or datetime.now(timezone.utc)
    timestamp = instant.strftime("%Y%m%dT%H%M%S%fZ")
    safe_job_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(job_id))
    safe_kind = re.sub(r"[^A-Za-z0-9_-]", "_", kind)
    return f"{safe_kind}_{safe_job_id}_{timestamp}"


def sanitize_html(html: str) -> str:
    """Redact common credentials and contact values from saved browser markup."""
    cleaned = re.sub(
        r"(?is)(<input\b[^>]*\bvalue\s*=\s*)([\"']).*?\2",
        r"\1\2[REDACTED]\2",
        html or "",
    )
    cleaned = re.sub(
        r"(?is)(<textarea\b[^>]*>).*?(</textarea>)",
        r"\1[REDACTED]\2",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        cleaned,
    )
    cleaned = re.sub(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)", "[REDACTED_PHONE]", cleaned)
    return cleaned
