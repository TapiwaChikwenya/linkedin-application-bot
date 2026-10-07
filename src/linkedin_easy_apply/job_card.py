"""Read LinkedIn search-result cards from light-DOM HTML without opening the job page."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser

import config

APPLY_KIND_EASY = "easy_apply"
APPLY_KIND_APPLIED = "already_applied"
APPLY_KIND_EXTERNAL = "external"
APPLY_KIND_MISSING = "missing"

SKIP_TITLE_FILTER = "Skipped by title/company filter"
SKIP_WORKPLACE = "Skipped by workplace filter"
SKIP_ALREADY_APPLIED = "Already applied"
SKIP_NO_EASY_APPLY = "No Easy Apply on search card"
SKIP_EXTERNAL = "Off-site apply, not Easy Apply"
SKIP_UNKNOWN_CARD = "Search card title missing; skipped without opening"

_ON_SITE = re.compile(r"\bon[\s-]?site\b", re.IGNORECASE)
_REMOTE_WORK = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_WORK = re.compile(r"\bhybrid\b", re.IGNORECASE)

TITLE_CLASS_HINTS = (
    "job-card-list__title",
    "job-card-container__link",
    "artdeco-entity-lockup__title",
)
COMPANY_CLASS_HINTS = (
    "job-card-container__primary-description",
    "job-card-container__company-name",
    "artdeco-entity-lockup__subtitle",
)
LOCATION_CLASS_HINTS = (
    "job-card-container__metadata-item",
    "job-card-container__metadata-wrapper",
)
APPLY_CLASS_HINTS = (
    "job-card-container__apply-method",
    "job-card-container__footer-job-state",
    "job-card-container__footer-item",
    "job-card-list__footer",
)
IGNORE_CLASS_HINTS = (
    "visually-hidden",
    "a11y-text",
    "sr-only",
    "screen-reader",
)
GENERIC_LABELS = frozenset(
    {
        "easy apply",
        "linkedin apply",
        "apply",
        "applied",
        "promoted",
        "viewed",
        "linkedin",
        "jobs",
        "dismiss",
        "skip",
        "next",
        "with verification",
    }
)

_VERIFICATION_PREFIX = re.compile(r"^\s*with verification\s+", re.IGNORECASE)
_ARIA_TITLE_AT_COMPANY = re.compile(
    r"^(?:(?:easy apply|linkedin apply|apply)(?: to)?\s+)?"
    r"(?:with verification\s+)?(.+?) at (.+?)\.?$",
    re.IGNORECASE,
)
_ALREADY_APPLIED = re.compile(
    r"\b(already applied|application submitted|you(?:['’]ve| have)? applied)\b",
    re.IGNORECASE,
)
_APPLIED_TOKEN = re.compile(r"(?:^|[\s·•,])applied(?:$|[\s·•,])", re.IGNORECASE)
_JOB_VIEW_ID = re.compile(r"/jobs/view/(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class SearchCard:
    job_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    apply_kind: str = APPLY_KIND_MISSING
    source: str = "html"
    snippet: str = ""


def normalize_job_id(raw: str) -> str:
    """Accept numeric ids, URNs, and /jobs/view/ URLs."""
    text = (raw or "").strip()
    if not text:
        return ""
    match = _JOB_VIEW_ID.search(text)
    if match:
        return match.group(1)
    tail = text.split(":")[-1]
    digits: list[str] = []
    for char in tail:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    return "".join(digits)


def clean_label(text: str) -> str:
    cleaned = " ".join((text or "").replace("\xa0", " ").split())
    cleaned = _VERIFICATION_PREFIX.sub("", cleaned)
    return cleaned.strip(" .")


def parse_aria_label(label: str) -> tuple[str, str]:
    """Return (title, company) from a card or overlay aria-label."""
    cleaned = clean_label(label)
    if not cleaned or cleaned.lower() in GENERIC_LABELS:
        return "", ""
    match = _ARIA_TITLE_AT_COMPANY.match(cleaned)
    if match:
        title = clean_label(match.group(1))
        company = clean_label(match.group(2))
        if title.lower() in GENERIC_LABELS:
            return "", company
        return title, company
    if cleaned.lower() in GENERIC_LABELS:
        return "", ""
    return cleaned, ""


def classify_card_apply(text: str, href: str = "", class_name: str = "") -> str:
    """Classify Easy Apply / applied / external from card footer signals."""
    blob = " ".join(filter(None, [text, href, class_name])).lower().replace("&amp;", "&")
    if not blob.strip():
        return APPLY_KIND_MISSING
    if _ALREADY_APPLIED.search(blob) or (
        _APPLIED_TOKEN.search(blob) and "easy apply" not in blob
    ):
        return APPLY_KIND_APPLIED
    if any(
        token in blob
        for token in ("company website", "apply on company", "offsite", "external site")
    ):
        return APPLY_KIND_EXTERNAL
    if (
        "opensduiapplyflow=true" in blob
        or "easy apply" in blob
        or "linkedin apply" in blob
        or "job-card-container__apply-method" in blob
        or "linkedin-bug" in blob
    ):
        return APPLY_KIND_EASY
    return APPLY_KIND_MISSING


def should_skip_job(title: str, company: str) -> bool:
    """Honor config allow/deny lists. Empty titles are not skipped here."""
    title_l = (title or "").lower()
    company_l = (company or "").lower()
    if not title_l.strip():
        return False
    for blocked in getattr(config, "blacklist", []):
        if blocked and blocked.lower() in company_l:
            return True
    for blocked in getattr(config, "blackListTitles", []):
        if blocked and blocked.lower() in title_l:
            return True
    only_companies = [item for item in getattr(config, "onlyApply", []) if item]
    if only_companies and not any(item.lower() in company_l for item in only_companies):
        return True
    only_titles = [item for item in getattr(config, "onlyApplyTitles", []) if item]
    return bool(only_titles) and not any(item.lower() in title_l for item in only_titles)


def allowed_workplaces() -> set[str]:
    allowed: set[str] = set()
    for item in getattr(config, "remote", []) or []:
        key = " ".join(str(item or "").lower().replace("_", " ").split())
        if key in {"on-site", "onsite", "on site"}:
            allowed.add("on-site")
        elif key == "remote":
            allowed.add("remote")
        elif key == "hybrid":
            allowed.add("hybrid")
    return allowed


def workplace_kind(*parts: str) -> str:
    blob = " ".join(part for part in parts if part)
    if not blob.strip():
        return ""
    on_site = bool(_ON_SITE.search(blob))
    remote = bool(_REMOTE_WORK.search(blob))
    hybrid = bool(_HYBRID_WORK.search(blob))
    if hybrid:
        return "hybrid"
    if remote:
        return "remote"
    if on_site:
        return "on-site"
    return ""


def should_skip_workplace(*parts: str) -> bool:
    """Skip listings whose workplace is present and outside config.remote."""
    allowed = allowed_workplaces()
    if not allowed:
        return False
    kind = workplace_kind(*parts)
    if not kind:
        return False
    return kind not in allowed


def card_skip_decision(
    card: SearchCard,
    *,
    applied_in_store: bool = False,
) -> tuple[str, str] | None:
    """Return (status, reason) when the job page must not be opened."""
    if applied_in_store or card.apply_kind == APPLY_KIND_APPLIED:
        return "already_applied", SKIP_ALREADY_APPLIED
    if not (card.job_id or "").strip():
        return "skipped_filter", SKIP_UNKNOWN_CARD
    if not (card.title or "").strip():
        return "skipped_filter", SKIP_UNKNOWN_CARD
    if should_skip_job(card.title, card.company):
        return "skipped_filter", SKIP_TITLE_FILTER
    if should_skip_workplace(card.location):
        return "skipped_filter", SKIP_WORKPLACE
    if card.apply_kind == APPLY_KIND_EXTERNAL:
        return "failed", SKIP_EXTERNAL
    if card.apply_kind != APPLY_KIND_EASY:
        return "failed", SKIP_NO_EASY_APPLY
    return None


def apply_card_fallbacks(
    card: SearchCard,
    *,
    aria_labels: list[str] | None = None,
    extra_text: str = "",
    extra_hrefs: list[str] | None = None,
    extra_job_id: str = "",
    extra_class: str = "",
) -> SearchCard:
    """Fill empty title/company/apply fields from aria-label and container attributes."""
    job_id = card.job_id or normalize_job_id(extra_job_id)
    title = card.title
    company = card.company
    location = card.location
    apply_kind = card.apply_kind
    source = card.source
    for label in aria_labels or []:
        parsed_title, parsed_company = parse_aria_label(label)
        if not title and parsed_title:
            title = parsed_title
            source = "aria_label"
        if not company and parsed_company:
            company = parsed_company
            if source == "html":
                source = "aria_label"
        kind = classify_card_apply(label)
        apply_kind = _prefer_apply_kind(apply_kind, kind)
    extra_kind = classify_card_apply(
        extra_text,
        " ".join(extra_hrefs or []),
        extra_class,
    )
    apply_kind = _prefer_apply_kind(apply_kind, extra_kind)
    if extra_hrefs:
        for href in extra_hrefs:
            job_id = job_id or normalize_job_id(href)
            apply_kind = _prefer_apply_kind(apply_kind, classify_card_apply("", href))
    return replace(
        card,
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        apply_kind=apply_kind,
        source=source,
        snippet=card.snippet or clean_label(extra_text)[:1200],
    )


def parse_search_card(html: str, job_id: str = "") -> SearchCard:
    """Parse one search-result <li> or .job-card-container fragment."""
    parser = _CardSignalParser()
    parser.feed(html or "")
    parser.close()
    card = parser.to_card()
    return apply_card_fallbacks(
        card,
        aria_labels=parser.aria_labels,
        extra_text=parser.all_text,
        extra_hrefs=parser.hrefs,
        extra_job_id=job_id or parser.job_id,
        extra_class=parser.class_blob,
    )


def parse_search_cards(html: str) -> list[SearchCard]:
    """Parse every `li[data-occludable-job-id]` in a search-results fixture."""
    parser = _SearchListParser()
    parser.feed(html or "")
    parser.close()
    return parser.cards


def jobs_allowed_to_open(
    cards: list[SearchCard],
    *,
    applied_ids: set[str] | None = None,
) -> list[SearchCard]:
    applied = applied_ids or set()
    allowed: list[SearchCard] = []
    for card in cards:
        if card_skip_decision(card, applied_in_store=card.job_id in applied) is None:
            allowed.append(card)
    return allowed


def _prefer_apply_kind(current: str, incoming: str) -> str:
    rank = {
        APPLY_KIND_APPLIED: 3,
        APPLY_KIND_EXTERNAL: 2,
        APPLY_KIND_EASY: 1,
        APPLY_KIND_MISSING: 0,
        "": 0,
    }
    if rank.get(incoming, 0) > rank.get(current, 0):
        return incoming
    return current or APPLY_KIND_MISSING


def _attr_map(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): (value or "") for key, value in attrs}


def _class_blob(attrs: dict[str, str]) -> str:
    return " ".join(
        filter(None, [attrs.get("class", ""), attrs.get("classname", "")])
    ).lower()


def _has_hint(blob: str, hints: tuple[str, ...]) -> bool:
    return any(hint in blob for hint in hints)


class _CardSignalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.job_id = ""
        self.aria_labels: list[str] = []
        self.hrefs: list[str] = []
        self.title_parts: list[str] = []
        self.company_parts: list[str] = []
        self.location_parts: list[str] = []
        self.apply_parts: list[str] = []
        self.all_text_parts: list[str] = []
        self.class_blob = ""
        self._stack: list[dict[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapped = _attr_map(attrs)
        classes = _class_blob(mapped)
        self.class_blob = (self.class_blob + " " + classes).strip()
        job_id = normalize_job_id(
            mapped.get("data-occludable-job-id")
            or mapped.get("data-job-id")
            or mapped.get("href")
            or ""
        )
        if job_id and not self.job_id:
            self.job_id = job_id
        aria = clean_label(mapped.get("aria-label", ""))
        if aria:
            self.aria_labels.append(aria)
        href = mapped.get("href", "")
        if href:
            self.hrefs.append(href)
        ignored = _has_hint(classes, IGNORE_CLASS_HINTS)
        self._stack.append(
            {
                "ignore": ignored,
                "title": (not ignored) and _has_hint(classes, TITLE_CLASS_HINTS),
                "company": (not ignored) and _has_hint(classes, COMPANY_CLASS_HINTS),
                "location": (not ignored) and _has_hint(classes, LOCATION_CLASS_HINTS),
                "apply": _has_hint(classes, APPLY_CLASS_HINTS),
            }
        )

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join((data or "").split())
        if not text:
            return
        flags = {"ignore": False, "title": False, "company": False, "location": False, "apply": False}
        for frame in self._stack:
            for key, value in flags.items():
                flags[key] = value or bool(frame.get(key))
        if flags["ignore"]:
            return
        self.all_text_parts.append(text)
        if flags["title"]:
            self.title_parts.append(text)
        if flags["company"]:
            self.company_parts.append(text)
        if flags["location"]:
            self.location_parts.append(text)
        if flags["apply"]:
            self.apply_parts.append(text)

    @property
    def all_text(self) -> str:
        return " ".join(self.all_text_parts)

    def to_card(self) -> SearchCard:
        title = clean_label(" ".join(self.title_parts))
        company = clean_label(" ".join(self.company_parts))
        location = clean_label(" ".join(self.location_parts))
        apply_text = " ".join(self.apply_parts) or self.all_text
        apply_kind = classify_card_apply(
            apply_text,
            " ".join(self.hrefs),
            self.class_blob,
        )
        if title.lower() in GENERIC_LABELS:
            title = ""
        return SearchCard(
            job_id=self.job_id,
            title=title,
            company=company,
            location=location,
            apply_kind=apply_kind,
            source="html",
            snippet=clean_label(self.all_text)[:1200],
        )


class _SearchListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[SearchCard] = []
        self._current: _CardSignalParser | None = None
        self._li_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapped = _attr_map(attrs)
        if (
            tag == "li"
            and mapped.get("data-occludable-job-id")
            and self._current is None
        ):
            self._current = _CardSignalParser()
            self._li_depth = 1
            self._current.handle_starttag(tag, attrs)
            return
        if self._current is None:
            return
        if tag == "li":
            self._li_depth += 1
        self._current.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        self._current.handle_endtag(tag)
        if tag == "li":
            self._li_depth -= 1
            if self._li_depth <= 0:
                self._current.close()
                self.cards.append(
                    apply_card_fallbacks(
                        self._current.to_card(),
                        aria_labels=self._current.aria_labels,
                        extra_text=self._current.all_text,
                        extra_hrefs=self._current.hrefs,
                        extra_job_id=self._current.job_id,
                        extra_class=self._current.class_blob,
                    )
                )
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.handle_data(data)
