"""Semantic Easy Apply detection. Closed shadows are pierced via WebDriver, not JS."""

from __future__ import annotations

import re

INTEROP_HOST_SELECTOR = (
    "#interop-outlet[data-testid='interop-shadowdom'], [data-testid='interop-shadowdom']"
)

DIALOG_SELECTORS = (
    "[role='dialog']",
    "dialog[open]",
    "[aria-modal='true']",
    "[data-testid='dialog']",
    "[data-test-modal]",
    ".jobs-easy-apply-modal",
    ".jobs-easy-apply-content",
    ".artdeco-modal",
)

FIELD_SELECTORS = (
    "input:not([type='hidden']):not([type='submit']):not([type='button'])",
    "textarea",
    "select",
    "[role='combobox']",
    "[role='textbox']",
    "[role='radio']",
    "[role='checkbox']",
    "[role='listbox']",
    "[contenteditable='true']",
    "fieldset[role='radiogroup']",
    "[role='radiogroup']",
)

ACTION_BUTTON_SELECTOR = "button, [role='button']"
PROGRESS_SELECTOR = "[role='progressbar'], progress"
CAROUSEL_DECOY_TESTIDS = frozenset(
    {
        "carousel-inline-right-button",
        "carousel-inline-left-button",
    }
)

ACTION_LABELS = {
    "submit": ("submit application", "submit"),
    "review": ("review application", "review"),
    "next": ("next", "continue"),
}


def normalize_accessible_name(text: str | None) -> str:
    """Normalize button text/aria-label for stable semantic matching."""
    return " ".join((text or "").casefold().split())


def is_carousel_decoy(testid: str = "", class_name: str = "") -> bool:
    """Job-page carousel chevrons share aria-label=Next with Easy Apply."""
    blob = normalize_accessible_name(f"{testid} {class_name}")
    if not blob:
        return False
    if testid.strip() in CAROUSEL_DECOY_TESTIDS:
        return True
    return "carousel-inline-right-button" in blob or "carousel-inline-left-button" in blob


def _name_matches_action(name: str, action: str) -> bool:
    return any(name == label or name.startswith(label + " ") for label in ACTION_LABELS[action])


def classify_application_action(
    text: str = "",
    aria_label: str = "",
    testid: str = "",
    class_name: str = "",
) -> str:
    """Classify an application action from accessible names, not generated CSS classes."""
    if is_carousel_decoy(testid, class_name):
        return ""
    # Icon-style Next has an exact aria-label and no (or junk) visible text.
    for candidate in (
        normalize_accessible_name(aria_label),
        normalize_accessible_name(text),
    ):
        if not candidate:
            continue
        for action in ("submit", "review", "next"):
            if _name_matches_action(candidate, action):
                return action
    return ""


def is_application_container_candidate(
    *,
    role: str = "",
    tag_name: str = "",
    aria_modal: str = "",
    text: str = "",
    has_progress: bool = False,
    field_count: int = 0,
    action_names: tuple[str, ...] = (),
) -> bool:
    """Score semantic facts extracted from a possible Easy Apply root.

    Native input counts may be zero when contact fields live in nested closed
    shadows as comboboxes. Apply-to copy, Contact info, a 0% progress bar, and a
    footer Next/Review/Submit action are enough.
    """
    semantic_dialog = (
        normalize_accessible_name(role) == "dialog"
        or normalize_accessible_name(tag_name) == "dialog"
        or normalize_accessible_name(aria_modal) == "true"
    )
    normalized_text = normalize_accessible_name(text)
    application_copy = bool(
        re.search(r"\b(apply to|contact info|application)\b", normalized_text)
    )
    has_action = any(action in {"next", "review", "submit"} for action in action_names)
    if not has_action:
        return False
    if semantic_dialog or has_progress or application_copy:
        return True
    return field_count > 0


DEEP_APPLICATION_CONTAINER_JS = r"""
const selectors = arguments[0];

function visible(el) {
  if (!el || !(el instanceof Element)) return false;
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" &&
         rect.width > 0 && rect.height > 0;
}

function isCarousel(button) {
  const testid = button.getAttribute("data-testid") || "";
  return testid === "carousel-inline-right-button" ||
         testid === "carousel-inline-left-button";
}

function roots(start) {
  const found = [start];
  for (let i = 0; i < found.length; i++) {
    const root = found[i];
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) found.push(el.shadowRoot);
      if (el.tagName === "IFRAME") {
        try {
          if (el.contentDocument) found.push(el.contentDocument);
        } catch (_) {
          // Cross-origin frames are intentionally ignored.
        }
      }
    }
  }
  return found;
}

function actionName(button) {
  if (isCarousel(button)) return "";
  const aria = (button.getAttribute("aria-label") || "").trim().toLowerCase().replace(/\s+/g, " ");
  const text = (button.innerText || "").trim().toLowerCase().replace(/\s+/g, " ");
  for (const name of [aria, text]) {
    if (name === "submit" || name.startsWith("submit application") || name.startsWith("submit ")) return "submit";
    if (name === "review" || name.startsWith("review application") || name.startsWith("review ")) return "review";
    if (name === "next" || name.startsWith("next ") ||
        name === "continue" || name.startsWith("continue ")) return "next";
  }
  return "";
}

const allRoots = roots(document);
const candidates = [];
for (const root of allRoots) {
  for (const selector of selectors) {
    for (const element of root.querySelectorAll(selector)) candidates.push(element);
  }
}

for (const root of allRoots) {
  for (const button of root.querySelectorAll("button,[role='button']")) {
    if (!actionName(button) || !visible(button)) continue;
    let node = button.parentElement;
    while (node) {
      const fields = node.querySelectorAll(
        "input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox']," +
        "[role='combobox'],[role='textbox'],[contenteditable='true']"
      ).length;
      const text = (node.innerText || "").toLowerCase();
      const signal = node.querySelector("[role='progressbar'],progress") ||
        /\b(apply to|contact info|application)\b/.test(text);
      if (signal) {
        candidates.push(node);
        break;
      }
      if (fields) {
        node = node.parentElement;
        continue;
      }
      node = node.parentElement;
    }
  }
}

const unique = [...new Set(candidates)];
for (const element of unique) {
  if (!visible(element)) continue;
  const actions = [...element.querySelectorAll("button,[role='button']")]
    .filter(visible).map(actionName).filter(Boolean);
  const fields = element.querySelectorAll(
    "input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox']," +
    "[role='combobox'],[role='textbox'],[contenteditable='true']"
  ).length;
  const text = (element.innerText || "").toLowerCase();
  const semantic = element.matches("[role='dialog'],dialog,[aria-modal='true']");
  const signal = element.querySelector("[role='progressbar'],progress") ||
    /\b(apply to|contact info|application)\b/.test(text);
  if (actions.length && (semantic || signal || fields)) return element;
}
return null;
"""


DEEP_APPLICATION_ACTION_JS = r"""
const container = arguments[0];
const wanted = new Set(arguments[1]);

function visible(el) {
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" &&
         rect.width > 0 && rect.height > 0 && !el.disabled &&
         el.getAttribute("aria-disabled") !== "true";
}

function isCarousel(button) {
  const testid = button.getAttribute("data-testid") || "";
  return testid === "carousel-inline-right-button" ||
         testid === "carousel-inline-left-button";
}

function actionName(button) {
  if (isCarousel(button)) return "";
  const aria = (button.getAttribute("aria-label") || "").trim().toLowerCase().replace(/\s+/g, " ");
  const text = (button.innerText || "").trim().toLowerCase().replace(/\s+/g, " ");
  for (const name of [aria, text]) {
    if (name === "submit" || name.startsWith("submit application") || name.startsWith("submit ")) return "submit";
    if (name === "review" || name.startsWith("review application") || name.startsWith("review ")) return "review";
    if (name === "next" || name.startsWith("next ") ||
        name === "continue" || name.startsWith("continue ")) return "next";
  }
  return "";
}

const roots = [container];
try {
  if (container.parentElement) roots.push(container.parentElement);
} catch (_) {
  // Footer may sit on a sibling of a content-only container.
}
for (let i = 0; i < roots.length; i++) {
  const root = roots[i];
  if (!root || !root.querySelectorAll) continue;
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) roots.push(el.shadowRoot);
  }
}
for (const action of arguments[1]) {
  for (const root of roots) {
    if (!root || !root.querySelectorAll) continue;
    for (const button of root.querySelectorAll("button,[role='button']")) {
      if (actionName(button) === action && visible(button)) return button;
    }
  }
}
return null;
"""
