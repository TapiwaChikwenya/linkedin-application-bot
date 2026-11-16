"""Read the current LinkedIn job page without hashed CSS class names.

The job view puts location, posted time, and applicant count on one line
("United States · 6 days ago · Over 100 applicants") and the description
under an "About the job" block. Those anchors stay stable when the class
hash changes.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_PRIMARY_RE = re.compile(
    r"^(?P<location>.+?)\s*[·•|]\s*"
    r"(?P<posted>(?:reposted\s+)?(?:\d+\s+\w+\s+ago|today|just now|yesterday))\s*[·•|]\s*"
    r"(?P<applicants>.+?\bapplicants?)\s*$",
    re.IGNORECASE,
)
_WORKPLACE = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "on-site": "On-site",
    "onsite": "On-site",
    "on site": "On-site",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg"}
_SECTION_ENDS = (
    "about the company",
    "set alert",
    "people also viewed",
    "similar jobs",
    "interested in this job",
)


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _cut_section(text: str) -> str:
    """Drop text that begins a later job-page section, including an unclosed box."""
    cleaned = _clean(text)
    lowered = cleaned.casefold()
    cut = len(cleaned)
    for marker in _SECTION_ENDS:
        index = lowered.find(marker)
        if index >= 0:
            cut = min(cut, index)
    return _clean(cleaned[:cut])


class _JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.company = ""
        self.description = ""
        self.paragraphs: list[str] = []
        self.workplaces: list[str] = []
        self._skip = 0
        self._await_box = False
        self._box_depth = 0
        self._h1_depth = 0
        self._anchor_href = ""
        self._anchor_parts: list[str] = []
        self._p_parts: list[str] | None = None
        self._h1_parts: list[str] = []
        self._box_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapped = {key.lower(): (value or "") for key, value in attrs}
        name = tag.lower()
        if name in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        testid = mapped.get("data-testid", "").lower()
        if self._box_depth:
            self._box_depth += 1
        elif self._await_box and testid == "expandable-text-box" and not self.description:
            self._box_depth = 1
            self._box_parts = []
        if name == "h1" and not self.title:
            self._h1_depth = 1
            self._h1_parts = []
        elif self._h1_depth:
            self._h1_depth += 1
        if name == "a" and not self.company:
            href = mapped.get("href", "")
            if "/company/" in href.lower():
                self._anchor_href = href
                self._anchor_parts = []
        elif self._anchor_href:
            pass
        if name == "p" and self._p_parts is None:
            self._p_parts = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if name == "p" and self._p_parts is not None:
            text = _clean(" ".join(self._p_parts))
            if text:
                self.paragraphs.append(text)
            self._p_parts = None
        if self._h1_depth:
            self._h1_depth -= 1
            if self._h1_depth <= 0:
                self.title = _clean(" ".join(self._h1_parts))
                self._h1_depth = 0
        if self._anchor_href and name == "a":
            company = _clean(" ".join(self._anchor_parts))
            if company and company.lower() not in {"linkedin"}:
                self.company = company
            self._anchor_href = ""
            self._anchor_parts = []
        if self._box_depth:
            self._box_depth -= 1
            if self._box_depth <= 0:
                text = _cut_section(" ".join(self._box_parts))
                if text and not self.description:
                    self.description = text
                    self._await_box = False
                self._box_parts = []
                self._box_depth = 0

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = _clean(data)
        if not text:
            return
        if self._h1_depth:
            self._h1_parts.append(text)
        if self._anchor_href:
            self._anchor_parts.append(text)
        if self._p_parts is not None:
            self._p_parts.append(text)
        if text.casefold() == "about the job" and not self.description:
            self._await_box = True
        if self._box_depth:
            if _clean(text).casefold() in _SECTION_ENDS:
                self.description = _cut_section(" ".join(self._box_parts))
                self._box_parts = []
                self._box_depth = 0
                self._await_box = False
                return
            self._box_parts.append(text)
        workplace = _WORKPLACE.get(text.casefold())
        if workplace:
            self.workplaces.append(workplace)

    def close(self) -> None:
        super().close()
        if self._box_parts and not self.description:
            text = _cut_section(" ".join(self._box_parts))
            if text:
                self.description = text
                self._await_box = False
        elif self.description:
            self.description = _cut_section(self.description)


def parse_job_page(html: str) -> dict[str, str]:
    """Return title, company, location, workplace, posted, applicants, description."""
    parser = _JobPageParser()
    try:
        parser.feed(html or "")
        parser.close()
    except (ValueError, AssertionError):
        return {
            "title": "",
            "company": "",
            "location": "",
            "workplace": "",
            "posted": "",
            "applications": "",
            "description": "",
        }
    location = ""
    posted = ""
    applications = ""
    for paragraph in parser.paragraphs:
        match = _PRIMARY_RE.match(paragraph)
        if not match:
            continue
        location = _clean(match.group("location"))
        posted = _clean(match.group("posted"))
        applications = _clean(match.group("applicants"))
        break
    description = parser.description
    if len(description) > 4000:
        description = description[:4000]
    return {
        "title": parser.title,
        "company": parser.company,
        "location": location,
        "workplace": parser.workplaces[0] if parser.workplaces else "",
        "posted": posted,
        "applications": applications,
        "description": description,
    }
