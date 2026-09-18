"""Operator job metrics: location, sector/market taxonomy, applied vs skipped."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

SECTORS = (
    "healthcare",
    "finance",
    "insurance",
    "government",
    "staffing",
    "technology",
    "other",
)
MARKETS = (
    "hospital",
    "health",
    "bank",
    "fintech",
    "insurance",
    "government",
    "staffing",
    "recruiting",
    "software",
    "data",
    "other",
)
MARKET_TO_SECTOR = {
    "hospital": "healthcare",
    "health": "healthcare",
    "bank": "finance",
    "fintech": "finance",
    "insurance": "insurance",
    "government": "government",
    "staffing": "staffing",
    "recruiting": "staffing",
    "software": "technology",
    "data": "technology",
    "other": "other",
}
OUTCOMES = ("applied", "skipped", "failed", "seen")
SKIP_STATUSES = frozenset(
    {"skipped_filter", "skipped_fit", "already_applied", "page_timeout"}
)
METRICS_WINDOWS = ("today", "7d")
SNIPPET_LIMIT = 1200
LOCATION_LIMIT = 200
CLASSIFY_TIMEOUT_SEC = 15
CLASSIFY_NUM_PREDICT = 80

# Company/industry markets first so a software role at a hospital stays healthcare.
_COMPANY_MARKET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "hospital",
        ("medical center", "hospital", "hospitals"),
    ),
    (
        "insurance",
        ("insurance", "insurer", "underwriting", "actuarial"),
    ),
    (
        "bank",
        (
            "morgan stanley",
            "goldman sachs",
            "jp morgan",
            "jpmorgan",
            "credit union",
            "blackrock",
            "fidelity",
            "banking",
            "bank",
        ),
    ),
    (
        "fintech",
        ("financial technology", "fintech", "payments platform"),
    ),
    (
        "government",
        (
            "public sector",
            "civil service",
            "department of",
            "municipality",
            "municipal",
            "federal government",
            "government",
            "usajobs",
        ),
    ),
    (
        "staffing",
        (
            "staff augmentation",
            "insight global",
            "robert half",
            "tek systems",
            "staffing",
            "randstad",
            "kforce",
            "rpo",
            "epam",
        ),
    ),
    (
        "recruiting",
        ("talent acquisition", "recruiting", "recruiter", "recruitment"),
    ),
    (
        "health",
        (
            "health care",
            "healthcare",
            "unitedhealth",
            "pharmaceutical",
            "biotech",
            "clinic",
            "nursing",
            "pharma",
            "medical",
            "cigna",
            "health",
        ),
    ),
)
_TITLE_MARKET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "recruiting",
        ("talent acquisition", "recruiting", "recruiter", "recruitment"),
    ),
    (
        "data",
        (
            "data engineer",
            "data scientist",
            "data analyst",
            "data platform",
            "analytics",
            "databricks",
            "snowflake",
            "data",
        ),
    ),
    (
        "software",
        (
            "full stack",
            "fullstack",
            "software",
            "developer",
            "programmer",
            "devops",
            "saas",
        ),
    ),
)
_ALL_MARKET_KEYWORDS = _COMPANY_MARKET_KEYWORDS + _TITLE_MARKET_KEYWORDS

CLASSIFY_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sector": {"type": "string", "enum": list(SECTORS)},
        "market": {"type": "string", "enum": list(MARKETS)},
    },
    "required": ["sector", "market"],
}

_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass(frozen=True)
class JobClassification:
    sector: str
    market: str
    location_text: str
    source: str
    outcome: str = ""

    def as_payload(
        self,
        *,
        job_id: str = "",
        title: str = "",
        company: str = "",
        outcome: str = "",
    ) -> dict[str, str]:
        resolved = outcome or self.outcome
        return {
            "location_text": self.location_text,
            "sector": self.sector,
            "market": self.market,
            "job_id": str(job_id or ""),
            "title": compact_text(title, 200),
            "company": compact_text(company, 200),
            "outcome": resolved,
            "source": self.source,
        }


def compact_text(value: str, limit: int = SNIPPET_LIMIT) -> str:
    return " ".join(str(value or "").split())[:limit]


def outcome_from_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value == "applied":
        return "applied"
    if value == "failed":
        return "failed"
    if value in SKIP_STATUSES:
        return "skipped"
    if value in OUTCOMES:
        return value
    return "seen"


def _word_pattern(token: str) -> re.Pattern[str]:
    cached = _WORD_RE_CACHE.get(token)
    if cached is not None:
        return cached
    if " " in token:
        pattern = re.compile(re.escape(token))
    else:
        pattern = re.compile(r"\b" + re.escape(token) + r"\b")
    _WORD_RE_CACHE[token] = pattern
    return pattern


def _blob_matches(blob: str, keywords: tuple[str, ...]) -> bool:
    return any(_word_pattern(keyword).search(blob) for keyword in keywords)


def _first_market(
    blob: str,
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    if not blob:
        return ""
    for market, keywords in groups:
        if _blob_matches(blob, keywords):
            return market
    return ""


def classify_taxonomy(
    title: str = "",
    company: str = "",
    snippet: str = "",
    location: str = "",
) -> JobClassification:
    """Keyword taxonomy. Company industry wins over title skills."""
    title_text = compact_text(title, 400).lower()
    company_text = compact_text(company, 400).lower()
    snippet_text = compact_text(snippet, SNIPPET_LIMIT).lower()
    title_blob = f"{title_text} {snippet_text}".strip()
    combined = f"{title_text} {company_text} {snippet_text}".strip()
    market = _first_market(company_text, _COMPANY_MARKET_KEYWORDS)
    if not market:
        market = _first_market(title_blob, _TITLE_MARKET_KEYWORDS)
    if not market:
        market = _first_market(combined, _ALL_MARKET_KEYWORDS)
    market = market if market in MARKETS else "other"
    sector = MARKET_TO_SECTOR.get(market, "other")
    if sector not in SECTORS:
        sector = "other"
    return JobClassification(
        sector=sector,
        market=market,
        location_text=compact_text(location, LOCATION_LIMIT),
        source="taxonomy",
    )


def _normalize_label(value: str, allowed: tuple[str, ...]) -> str:
    text = compact_text(value, 40).lower()
    if text == "tech":
        text = "technology"
    return text if text in allowed else ""


def _llm_classification(
    title: str,
    company: str,
    snippet: str,
    location_text: str,
    generate_json: Any,
    config_module: Any | None,
) -> JobClassification | None:
    payload = {
        "title": compact_text(title, 200),
        "company": compact_text(company, 200),
        "snippet": compact_text(snippet, SNIPPET_LIMIT),
    }
    prompt = (
        "Classify this LinkedIn job into one sector and one market.\n"
        "Use only title, company, and snippet. Do not invent employers.\n"
        "Allowed sectors: " + ", ".join(SECTORS) + ".\n"
        "Allowed markets: " + ", ".join(MARKETS) + ".\n"
        "technology is the tech bucket. other if it is unclear.\n"
        "Return JSON only with this shape:\n"
        '{"sector":"technology","market":"software"}\n\n'
        + json.dumps(payload, ensure_ascii=True)
    )
    raw = generate_json(
        prompt,
        CLASSIFY_RESPONSE_FORMAT,
        config_module=config_module,
        timeout=CLASSIFY_TIMEOUT_SEC,
        num_predict=CLASSIFY_NUM_PREDICT,
    )
    if not isinstance(raw, dict) or not raw:
        return None
    sector = _normalize_label(str(raw.get("sector") or ""), SECTORS)
    market = _normalize_label(str(raw.get("market") or ""), MARKETS)
    if not sector or not market:
        return None
    expected = MARKET_TO_SECTOR.get(market)
    if expected and expected != "other" and sector == "other":
        sector = expected
    if expected and expected != "other" and sector not in {expected, "other"}:
        sector = expected
    return JobClassification(
        sector=sector,
        market=market,
        location_text=location_text,
        source="llm",
    )


def classify_job(
    title: str = "",
    company: str = "",
    snippet: str = "",
    location: str = "",
    *,
    already_open: bool = False,
    generate_json: Any | None = None,
    config_module: Any | None = None,
    sector: str = "",
    market: str = "",
    outcome: str = "",
    status: str = "",
) -> JobClassification:
    """Taxonomy first. Llama only if the job page is already open and labels are other."""
    location_text = compact_text(location, LOCATION_LIMIT)
    resolved_outcome = outcome or outcome_from_status(status)
    preset_sector = _normalize_label(sector, SECTORS)
    preset_market = _normalize_label(market, MARKETS)
    if preset_sector and preset_market:
        return JobClassification(
            sector=preset_sector,
            market=preset_market,
            location_text=location_text,
            source="preset",
            outcome=resolved_outcome,
        )
    taxonomy = classify_taxonomy(title, company, snippet, location_text)
    if taxonomy.sector != "other":
        return JobClassification(
            sector=taxonomy.sector,
            market=taxonomy.market,
            location_text=location_text,
            source=taxonomy.source,
            outcome=resolved_outcome,
        )
    if not already_open:
        return JobClassification(
            sector=taxonomy.sector,
            market=taxonomy.market,
            location_text=location_text,
            source=taxonomy.source,
            outcome=resolved_outcome,
        )
    producer = generate_json
    if producer is None:
        from linkedin_easy_apply.llm import generate_json as default_generate_json

        producer = default_generate_json
    try:
        llm_result = _llm_classification(
            title,
            company,
            snippet,
            location_text,
            producer,
            config_module,
        )
    except (OSError, TimeoutError, TypeError, ValueError):
        llm_result = None
    if llm_result is None:
        return JobClassification(
            sector=taxonomy.sector,
            market=taxonomy.market,
            location_text=location_text,
            source=taxonomy.source,
            outcome=resolved_outcome,
        )
    return JobClassification(
        sector=llm_result.sector,
        market=llm_result.market,
        location_text=location_text,
        source=llm_result.source,
        outcome=resolved_outcome,
    )


def parse_search_card_html(html: str) -> dict[str, str]:
    """Extract job_id, title, company, and location from a search-result card."""
    from linkedin_easy_apply.job_card import parse_search_card

    card = parse_search_card(html or "")
    location = compact_text(card.location, LOCATION_LIMIT)
    return {
        "job_id": str(card.job_id or ""),
        "title": compact_text(card.title, 200),
        "company": compact_text(card.company, 200),
        "location": location,
        "location_text": location,
        "snippet": compact_text(card.snippet, SNIPPET_LIMIT),
    }


def search_card_metrics(
    *,
    job_id: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    html: str = "",
) -> dict[str, str]:
    parsed = parse_search_card_html(html) if html else {}
    resolved_id = str(job_id or parsed.get("job_id") or "")
    resolved_title = title or str(parsed.get("title") or "")
    resolved_company = company or str(parsed.get("company") or "")
    resolved_location = location or str(parsed.get("location_text") or parsed.get("location") or "")
    classified = classify_taxonomy(
        resolved_title,
        resolved_company,
        "",
        resolved_location,
    )
    payload = classified.as_payload(
        job_id=resolved_id,
        title=resolved_title,
        company=resolved_company,
    )
    payload["page_open"] = "false"
    return payload


def metrics_payload_json(payload: dict[str, Any]) -> str:
    compact = {
        key: str(payload.get(key) or "")
        for key in (
            "location_text",
            "sector",
            "market",
            "job_id",
            "title",
            "company",
            "outcome",
            "source",
        )
    }
    return json.dumps(compact, ensure_ascii=True)


def empty_metrics_window(since: str, until: str) -> dict[str, Any]:
    return {
        "since": since,
        "until": until,
        "totals": {key: 0 for key in (*OUTCOMES, "total")},
        "by_sector": [],
        "by_market": [],
        "by_location": [],
        "by_outcome": [],
    }


def taxonomy_catalog() -> dict[str, list[str]]:
    return {"sectors": list(SECTORS), "markets": list(MARKETS), "outcomes": list(OUTCOMES)}
