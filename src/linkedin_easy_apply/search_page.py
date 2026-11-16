"""Find LinkedIn job cards without depending on one hashed class name.

LinkedIn has replaced ``/jobs/search/`` list markup with ``/jobs/search-results/``.
The stable contract is visible text and ``currentJobId``, not CSS classes:

- a result card's text names the job and says Easy Apply or Applied
- the selected card's id is the ``currentJobId`` query parameter
- clicking another card updates that parameter

``data-display-contents`` is the current semantic layout. When that attribute
goes away, the same script still returns role=button cards whose text matches
the contract.
"""

from __future__ import annotations

import re

COLLECT_CARDS_JS = r"""
const semanticSel = "div[role='button'][componentkey]:has(div[data-display-contents] > p[style])";
const broadSel = "div[role='button'][componentkey], a[componentkey]";
const current = new URL(location.href).searchParams.get("currentJobId") || "";
function linesOf(el) {
  const fromP = Array.from(el.querySelectorAll("p"))
    .map((node) => (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim())
    .filter((text) => text && text !== "\u00b7" && text !== "\u2022");
  if (fromP.length >= 2) return fromP;
  return (el.innerText || "")
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}
function listedJobId(el) {
  let node = el;
  for (let depth = 0; node && depth < 8; depth += 1) {
    if (node.getAttribute) {
      const direct = node.getAttribute("data-occludable-job-id")
        || node.getAttribute("data-job-id")
        || "";
      if (direct) return direct;
      const href = node.getAttribute("href") || "";
      if (href.indexOf("/jobs/view/") !== -1) return href;
    }
    if (node.querySelector) {
      const owned = node.querySelector(
        "[data-occludable-job-id], [data-job-id], a[href*='/jobs/view/']"
      );
      if (owned) {
        const id = owned.getAttribute("data-occludable-job-id")
          || owned.getAttribute("data-job-id")
          || owned.getAttribute("href")
          || "";
        if (id) return id;
      }
    }
    node = node.parentElement;
  }
  return "";
}
function payload(el) {
  const paras = linesOf(el);
  const text = (el.innerText || "").replace(/\s+/g, " ").trim();
  const selected = /^\s*selected\b/i.test(paras[0] || text);
  const listed = listedJobId(el);
  return {
    paras: paras,
    text: text.slice(0, 500),
    selected: selected,
    job_id: listed || (selected ? current : ""),
    component_key: el.getAttribute("componentkey") || ""
  };
}
let semantic = [];
try {
  semantic = Array.from(document.querySelectorAll(semanticSel)).map(payload);
} catch (err) {
  semantic = [];
}
const broad = Array.from(document.querySelectorAll(broadSel)).map(payload);
return {semantic: semantic, broad: broad};
"""

CLICK_CARD_JS = r"""
const index = arguments[0];
const key = arguments[1] || "";
function activate(el) {
  if (!el) return "";
  el.scrollIntoView({block: "center", inline: "nearest"});
  el.click();
  return "clicked";
}
if (key) {
  const nodes = document.querySelectorAll("[componentkey]");
  for (const el of nodes) {
    if (el.getAttribute("componentkey") === key) return activate(el);
  }
}
const sel = "div[role='button'][componentkey]:has(div[data-display-contents] > p[style])";
let cards = [];
try {
  cards = Array.from(document.querySelectorAll(sel));
} catch (err) {
  cards = Array.from(document.querySelectorAll("div[role='button'][componentkey]"));
}
return activate(cards[index]);
"""

APPLY_CONTROLS_JS = r"""
function norm(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
const found = [];
const seen = new Set();
const visit = (node) => {
  if (!node || !node.querySelectorAll) return;
  for (const el of node.querySelectorAll("button, a, [role='button']")) {
    if (seen.has(el)) continue;
    const aria = norm(el.getAttribute("aria-label"));
    const text = norm(el.innerText || el.textContent);
    const blob = (aria + " " + text).toLowerCase();
    if (!/easy apply|linkedin apply|already applied|apply to this job|apply on company|you applied/.test(blob)) {
      continue;
    }
    if (!aria && text.length > 180) continue;
    seen.add(el);
    found.push(el);
  }
  for (const el of node.querySelectorAll("*")) {
    if (el.shadowRoot) visit(el.shadowRoot);
  }
};
visit(document);
return found;
"""

_APPLIED_WORD = re.compile(r"(?:^|[\s·•])applied(?:$|[\s·•])", re.IGNORECASE)


def is_result_card_text(text: str) -> bool:
    """True for a job card's visible text, false for filter chips."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) < 40 or len(cleaned) > 900:
        return False
    lowered = cleaned.lower()
    if "easy apply" in lowered:
        return True
    return bool(_APPLIED_WORD.search(lowered))


def _drop_contained_cards(items: list[dict]) -> list[dict]:
    texts = [" ".join(str(item.get("text") or "").split()) for item in items]
    kept: list[dict] = []
    for index, item in enumerate(items):
        text = texts[index]
        contained = any(
            index != other
            and text
            and text in texts[other]
            and len(texts[other]) > len(text) + 15
            for other in range(len(texts))
        )
        if not contained:
            kept.append(item)
    return kept


def select_card_payloads(collected: dict | list | None) -> list[dict]:
    """Prefer the current semantic layout, then any role=button card text."""
    if isinstance(collected, list):
        return [item for item in collected if isinstance(item, dict)]
    if not isinstance(collected, dict):
        return []
    semantic = [item for item in collected.get("semantic") or [] if isinstance(item, dict)]
    if semantic:
        return semantic
    broad = [
        item
        for item in collected.get("broad") or []
        if isinstance(item, dict) and is_result_card_text(str(item.get("text") or ""))
    ]
    return _drop_contained_cards(broad)
