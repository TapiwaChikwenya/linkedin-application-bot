"""Classify application outcomes into operator metrics (sector, market, location)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

APPLIED = frozenset({"applied"})
SKIPPED = frozenset({"skipped_filter", "skipped_fit", "already_applied", "page_timeout"})
FAILED = frozenset({"failed"})

SECTOR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_eng", ("data engineer", "data platform", "etl", "warehouse", "databricks", "azure data", "spark", "airflow")),
    ("analytics", ("analytics engineer", "data analyst", "business intelligence", "bi engineer", "analytics")),
    ("sql", ("sql developer", "sql engineer", "database developer")),
    ("software", ("software engineer", "backend", "full stack", "fullstack", "sre", "devops")),
)
MARKET_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "staffing",
        (
            "staffing",
            "recruit",
            "talent acquisition",
            "rpo",
            "contracting",
            "epam",
            "infosys",
            "tcs ",
            "wipro",
            "cognizant",
            "accenture",
            "randstad",
            "kforce",
            "robert half",
            "tek systems",
            "insight global",
        ),
    ),
    (
        "finance",
        (
            "bank",
            "capital",
            "invest",
            "trading",
            "fintech",
            "insurance",
            "hedge",
            "wealth",
            "credit",
            "nasdaq",
            "visa",
            "mastercard",
            "fidelity",
            "blackrock",
            "jpmorgan",
            "jp morgan",
            "goldman",
            "morgan stanley",
        ),
    ),
    (
        "healthcare",
        (
            "health",
            "hospital",
            "pharma",
            "medical",
            "biotech",
            "clinic",
            "life science",
            "patient",
            "unitedhealth",
            "cigna",
        ),
    ),
    ("energy", ("energy", "oil", "gas", "utility", "solar", "renewable", "electric")),
    ("retail", ("retail", "commerce", "shopify", "walmart", "target", "costco")),
    ("government", ("government", "federal", "public sector", "defense", "department of")),
    (
        "tech",
        (
            "software",
            "saas",
            "cloud",
            "google",
            "microsoft",
            "meta",
            "apple",
            "amazon",
            "nvidia",
            "databricks",
            "snowflake",
            "platform",
            "openai",
        ),
    ),
)


def local_today(now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc).astimezone()
    return stamp.date().isoformat()


def _blob(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).casefold()


def classify_sector(title: str = "", company: str = "") -> str:
    text = _blob(title, company)
    for label, needles in SECTOR_RULES:
        if any(needle in text for needle in needles):
            return label
    return "other"


def classify_market(title: str = "", company: str = "") -> str:
    text = _blob(company, title)
    for label, needles in MARKET_RULES:
        if any(needle in text for needle in needles):
            return label
    return "other"


def classify_location(location: str = "") -> str:
    text = " ".join(str(location or "").split())
    return text or "unknown"


def outcome_bucket(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in APPLIED:
        return "applied"
    if value in SKIPPED:
        return "skipped"
    if value in FAILED:
        return "failed"
    return "other"


def _is_today(row: dict[str, Any], today: str) -> bool:
    seen = str(row.get("seen_at") or "")
    applied = str(row.get("applied_at") or "")
    return seen.startswith(today) or applied.startswith(today)


def _tally(
    rows: list[dict[str, Any]],
    key_fn,
    *,
    today_only: bool = False,
    today: str = "",
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        if today_only and not _is_today(row, today):
            continue
        label = str(key_fn(row) or "other")
        item = buckets.setdefault(
            label,
            {"key": label, "label": label, "applied": 0, "skipped": 0, "failed": 0, "seen": 0, "total": 0, "count": 0},
        )
        bucket = outcome_bucket(str(row.get("status") or ""))
        if bucket in {"applied", "skipped", "failed"}:
            item[bucket] += 1
        elif bucket == "other":
            item["seen"] += 1
        item["total"] += 1
        item["count"] += 1
    ranked = sorted(buckets.values(), key=lambda item: (-int(item["total"]), str(item["label"])))
    return ranked[:8]


def _outcome_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"applied": 0, "skipped": 0, "failed": 0, "seen": 0, "total": 0}
    for row in rows:
        bucket = outcome_bucket(str(row.get("status") or ""))
        if bucket in {"applied", "skipped", "failed"}:
            totals[bucket] += 1
        else:
            totals["seen"] += 1
        totals["total"] += 1
    return totals


def _by_outcome(totals: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": int(totals.get(key) or 0)}
        for key in ("applied", "skipped", "failed", "seen")
        if int(totals.get(key) or 0)
    ]


def _metrics_window(
    rows: list[dict[str, Any]],
    *,
    since: str,
    until: str,
) -> dict[str, Any]:
    totals = _outcome_totals(rows)
    return {
        "since": since,
        "until": until,
        "totals": totals,
        "by_sector": _tally(
            rows, lambda row: classify_sector(str(row.get("title") or ""), str(row.get("company") or ""))
        ),
        "by_market": _tally(
            rows, lambda row: classify_market(str(row.get("title") or ""), str(row.get("company") or ""))
        ),
        "by_location": _tally(rows, lambda row: classify_location(str(row.get("location") or ""))),
        "by_outcome": _by_outcome(totals),
    }


def build_metrics(
    jobs: list[dict[str, Any]] | None,
    *,
    today: str | None = None,
    source: str = "derived",
) -> dict[str, Any]:
    rows = [row for row in (jobs or []) if isinstance(row, dict)]
    day = today or local_today()
    today_rows = [row for row in rows if _is_today(row, day)]
    today_window = _metrics_window(today_rows, since=day, until=day)
    week_window = _metrics_window(rows, since=day, until=day)
    today_counts = today_window["totals"]
    return {
        "source": source,
        "today": today_window,
        "last_7_days": week_window,
        "applied_vs_skipped": {
            "applied": int(today_counts.get("applied") or 0),
            "skipped": int(today_counts.get("skipped") or 0),
            "failed": int(today_counts.get("failed") or 0),
        },
        "sectors": week_window["by_sector"],
        "markets": week_window["by_market"],
        "locations": week_window["by_location"],
    }
