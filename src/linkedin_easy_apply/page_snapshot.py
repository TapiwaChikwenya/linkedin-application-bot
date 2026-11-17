"""Read LinkedIn Easy Apply fields through light DOM and shadow roots."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_RADIO_HELPERS_JS = r"""
function normText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
function radioChoiceLabel(radio) {
  const aria = normText(radio.getAttribute("aria-label"));
  let node = radio.parentElement;
  for (let depth = 0; depth < 6 && node; depth += 1) {
    const found = [];
    const bits = node.querySelectorAll("p, span, label");
    for (const el of bits) {
      if (el.contains(radio)) continue;
      if (el.closest && el.closest("legend")) continue;
      const text = normText(el.innerText || el.textContent).replace(/\*+$/, "").trim();
      if (!text || text === aria) continue;
      if (text.length > 60) continue;
      if (aria && aria.toLowerCase().indexOf(text.toLowerCase()) === 0 && text.length > 24) continue;
      found.push(text);
    }
    if (found.length) {
      found.sort((a, b) => a.length - b.length || a.localeCompare(b));
      return found[0];
    }
    const role = (node.getAttribute && node.getAttribute("role")) || "";
    const tag = (node.tagName || "").toLowerCase();
    if (role === "radiogroup" || tag === "fieldset") break;
    node = node.parentElement;
  }
  const value = normText(radio.value);
  if (value && value.length <= 40 && !/^\[?redacted/i.test(value)) return value;
  if (aria && aria.length <= 40) return aria;
  return "";
}
function radioPrompt(host) {
  if (!host) return "";
  const legend = host.querySelector ? host.querySelector("legend") : null;
  const legendText = normText(legend && (legend.innerText || legend.textContent)).replace(/\*+$/, "").trim();
  if (legendText && legendText.length > 8 && !/^(yes|no)$/i.test(legendText)) return legendText;
  let prev = host.previousElementSibling;
  for (let i = 0; i < 4 && prev; i += 1) {
    const text = normText(prev.innerText || prev.textContent).replace(/\*+$/, "").trim();
    if (text && text.length > 12 && !/^(yes|no)$/i.test(text)) return text;
    prev = prev.previousElementSibling;
  }
  const radios = host.querySelectorAll ? host.querySelectorAll("[role='radio'], input[type='radio']") : [];
  const labels = [];
  for (const radio of radios) {
    const aria = normText(radio.getAttribute("aria-label"));
    if (aria) labels.push(aria);
  }
  if (labels.length && labels.every((item) => item === labels[0]) && labels[0].length > 12) return labels[0];
  return "";
}
"""

RADIO_CHOICE_JS = _RADIO_HELPERS_JS + "\nreturn radioChoiceLabel(arguments[0]);\n"

COLLECT_JS = _RADIO_HELPERS_JS + r"""
const root = arguments[0] || document.documentElement;
const selector = arguments[1];
const elements = [];
const visit = (node) => {
  if (!node || !node.querySelectorAll) return;
  elements.push(...node.querySelectorAll(selector));
  for (const el of node.querySelectorAll("*")) {
    if (el.shadowRoot) visit(el.shadowRoot);
  }
};
visit(root);
const metas = elements.map((el) => {
  const rootNode = el.getRootNode ? el.getRootNode() : document;
  const labelledBy = el.getAttribute("aria-labelledby");
  let labelled = "";
  if (labelledBy && rootNode.getElementById) {
    const node = rootNode.getElementById(labelledBy);
    labelled = (node && (node.innerText || node.textContent)) || "";
  }
  const labels = Array.from(el.labels || []).map((x) => x.innerText || x.textContent || "").join(" ");
  const group = el.closest(
    "fieldset, [role='group'], [role='radiogroup'], .fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-text-input, [data-test-form-element]"
  );
  const legend = group && group.querySelector ? group.querySelector("legend") : null;
  const groupText = legend
    ? (legend.innerText || legend.textContent || "")
    : (group ? (group.innerText || group.textContent || "").split("\n")[0] : "");
  let question = [labelled, labels, el.getAttribute("aria-label"), el.placeholder, el.name, groupText]
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  const tag = (el.tagName || "").toLowerCase();
  const type = (el.getAttribute("type") || "").toLowerCase();
  const role = (el.getAttribute("role") || "").toLowerCase();
  let kind = "text";
  if (tag === "select" || role === "listbox") kind = "select";
  else if (tag === "textarea" || el.isContentEditable || el.getAttribute("contenteditable") === "true") kind = "textarea";
  else if (type === "radio" || role === "radiogroup" || tag === "fieldset") kind = "radio";
  else if (type === "checkbox") kind = "checkbox";
  else if (type === "file") kind = "file";
  else if (type === "tel") kind = "tel";
  else if (type === "email") kind = "email";
  else if (type === "number") kind = "number";
  else if (role === "combobox") kind = "text";
  else if (role === "textbox") kind = "text";
  const options = [];
  if (kind === "select") {
    for (const opt of el.options || []) options.push((opt.text || opt.value || "").trim());
  } else if (kind === "radio") {
    const host = group || el;
    const prompt = radioPrompt(host);
    if (prompt) question = prompt;
    const radios = host.querySelectorAll("[role='radio'], input[type='radio']");
    const seen = new Set();
    for (const radio of radios) {
      const choice = radioChoiceLabel(radio);
      const key = choice.toLowerCase();
      if (!choice || seen.has(key)) continue;
      if (prompt && key === prompt.toLowerCase()) continue;
      seen.add(key);
      options.push(choice);
    }
  }
  const describedBy = el.getAttribute("aria-describedby") || "";
  let helperText = "";
  if (describedBy && rootNode.getElementById) {
    const helper = rootNode.getElementById(describedBy.split(/\s+/)[0]);
    helperText = (helper && (helper.innerText || helper.textContent)) || "";
  }
  const invalid = el.getAttribute("aria-invalid") === "true"
    || String(el.getAttribute("data-validation-state") || "").toLowerCase() === "invalid";
  const required = !!(
    el.required ||
    el.getAttribute("aria-required") === "true" ||
    /required/.test(question.toLowerCase()) ||
    /this field is required/i.test(helperText) ||
    invalid
  );
  const value = (el.value || el.getAttribute("aria-valuetext") || "").trim();
  const checked = !!(el.checked || el.getAttribute("aria-checked") === "true");
  return {
    question,
    kind,
    type,
    tag,
    id: el.id || "",
    name: el.name || "",
    value,
    checked,
    required,
    options,
    testid: el.getAttribute("data-testid") || "",
  };
});
return [metas, elements];
"""

VISIBLE_TEXT_JS = r"""
const root = arguments[0] || document.documentElement;
const limit = arguments[1] || 6000;
let text = "";
const walk = (node) => {
  if (!node || text.length >= limit) return;
  if (node.nodeType === Node.TEXT_NODE) {
    text += " " + (node.textContent || "");
    return;
  }
  if (node.shadowRoot) walk(node.shadowRoot);
  const children = node.childNodes || [];
  for (const child of children) walk(child);
};
walk(root);
return text.replace(/\s+/g, " ").trim().slice(0, limit);
"""

FIELD_SELECTOR = (
    "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='radio']), "
    "textarea, select, fieldset[role='radiogroup'], "
    "fieldset[data-test-form-builder-radio-button-form-component], [role='radiogroup'], "
    "[role='combobox'], [role='textbox'], [contenteditable='true']"
)


def field_kind_from_attrs(tag: str = "", role: str = "", input_type: str = "",
                          contenteditable: str = "") -> str:
    """Map collected control attributes to the worker's field kind."""
    tag_l = (tag or "").lower()
    role_l = (role or "").lower()
    type_l = (input_type or "").lower()
    if tag_l == "select" or role_l == "listbox":
        return "select"
    if tag_l == "textarea" or contenteditable in {"true", "plaintext-only"}:
        return "textarea"
    if type_l == "radio" or role_l == "radiogroup" or tag_l == "fieldset":
        return "radio"
    if type_l == "checkbox":
        return "checkbox"
    if type_l == "file":
        return "file"
    if type_l == "tel":
        return "tel"
    if type_l == "email":
        return "email"
    if type_l == "number":
        return "number"
    if role_l in {"combobox", "textbox"}:
        return "text"
    return "text"


_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_SELECT_PLACEHOLDERS = {
    "",
    "select an option",
    "select",
    "month",
    "year",
    "choose",
    "please select",
}
_PAGE_COUNTER_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\s+pages?\b", re.IGNORECASE)
_YEAR_OPTION_RE = re.compile(r"^(19|20)\d{2}$")
VALIDATION_SELECTOR = (
    "[role='alert'], .artdeco-inline-feedback__message, "
    "[data-testid='full-date-picker-error-message'], "
    "[data-testid='text-input-helper-text'], "
    "[data-testid='select-helper-text'], "
    "[data-testid*='error-message']"
)


def is_inline_validation(text: str) -> bool:
    """True for visible Easy Apply errors, including text with no role=alert."""
    cleaned = " ".join((text or "").split()).casefold()
    if not cleaned or len(cleaned) > 180:
        return False
    return any(
        phrase in cleaned
        for phrase in (
            "this field is required",
            "please enter a valid",
            "please make a selection",
            "enter a valid",
            "is invalid",
        )
    )


def page_counter_from_text(text: str) -> int | None:
    """Read the current page from '4/5 pages'. Percent progress is not a page."""
    match = _PAGE_COUNTER_RE.search(text or "")
    if not match:
        return None
    current = int(match.group(1))
    total = int(match.group(2))
    if current < 1 or total < 1 or current > total:
        return None
    return current


def _option_keys(field: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for option in field.get("options") or []:
        cleaned = " ".join(str(option).split()).casefold()
        if cleaned:
            keys.append(cleaned)
    return keys


def control_shape(field: dict[str, Any] | None) -> str:
    """Classify date pickers and month/year selects. Empty when it is neither."""
    info = field or {}
    testid = str(info.get("testid") or "").casefold()
    question = " ".join(str(info.get("question") or "").casefold().split())
    input_type = str(info.get("type") or "").casefold()
    if (
        "date-picker" in testid
        or input_type == "date"
        or "start date" in question
        or "date of birth" in question
        or question in {"date", "birth date"}
    ):
        return "date"
    options = _option_keys(info)
    month_hits = sum(1 for name in _MONTH_NAMES if name in options)
    if month_hits >= 6 or question == "month":
        return "month"
    year_hits = sum(1 for option in options if _YEAR_OPTION_RE.match(option))
    if year_hits >= 5 or question == "year":
        return "year"
    return ""


def select_value_is_placeholder(value: str) -> bool:
    return " ".join((value or "").split()).casefold() in _SELECT_PLACEHOLDERS


def is_availability_date(field: dict[str, Any] | None) -> bool:
    """Earliest/available start dates. Not birth dates or employment end dates."""
    question = " ".join(str((field or {}).get("question") or "").casefold().split())
    if any(token in question for token in ("birth", "dob", "end date", "graduation")):
        return False
    if "start date" in question or "available" in question or "earliest" in question:
        return True
    return control_shape(field) == "date" and "birth" not in question and not question


def temporal_side(field: dict[str, Any] | None) -> str:
    """'start' or 'end' when the label says so. Empty when the order decides."""
    question = " ".join(str((field or {}).get("question") or "").casefold().split())
    if any(token in question for token in ("end date", "end month", "end year", "until", "to date")):
        return "end"
    if question in {"to", "end"} or question.startswith("to "):
        return "end"
    if any(token in question for token in ("start", "from", "begin")):
        return "start"
    return ""


def group_work_history_fields(fields: list[dict[str, Any]] | None) -> list[list[int]]:
    """Group month/year selects into repeating employment roles, in document order."""
    form = list(fields or [])
    groups: list[list[int]] = []
    current: list[int] = []
    months_in_group = 0
    saw_temporal = False

    def close() -> None:
        nonlocal current, months_in_group, saw_temporal
        if current:
            groups.append(current)
        current = []
        months_in_group = 0
        saw_temporal = False

    for index, field in enumerate(form):
        question = " ".join(str(field.get("question") or "").casefold().split())
        kind = str(field.get("kind") or "")
        role_break = kind in {"text", "textarea"} and any(
            token in question
            for token in ("company", "employer", "job title", "position title", "your title")
        )
        if role_break and saw_temporal:
            close()
        shape = control_shape(field)
        if shape not in {"month", "year"}:
            continue
        # A third month, or an explicit new start after start+end, begins the next role.
        # Month, year, month, year stays one role: the second month is the end date.
        new_role = shape == "month" and (
            months_in_group >= 2
            or (
                months_in_group == 1
                and temporal_side(field) == "start"
                and any(temporal_side(form[i]) == "end" for i in current)
            )
        )
        if new_role:
            close()
        if shape == "month":
            months_in_group += 1
        current.append(index)
        saw_temporal = True
    close()
    return groups


def _slot_indexes(indexes: list[int], fields: list[dict[str, Any]], shape: str) -> tuple[int | None, int | None]:
    start_index = None
    end_index = None
    unlabeled: list[int] = []
    for index in indexes:
        if control_shape(fields[index]) != shape:
            continue
        side = temporal_side(fields[index])
        if side == "start" and start_index is None:
            start_index = index
        elif side == "end" and end_index is None:
            end_index = index
        else:
            unlabeled.append(index)
    for index in unlabeled:
        if start_index is None:
            start_index = index
        elif end_index is None:
            end_index = index
    return start_index, end_index


def _current_checkbox(fields: list[dict[str, Any]], start: int, stop: int) -> int | None:
    for index in range(start, stop):
        field = fields[index]
        question = str(field.get("question") or "").casefold()
        if str(field.get("kind") or "") == "checkbox" and "current" in question:
            return index
    return None


def _single_start_pair(indexes: list[int], fields: list[dict[str, Any]]) -> bool:
    """One start month and/or year, with no end slot. Config may fill that pair."""
    for shape in ("month", "year"):
        start_index, end_index = _slot_indexes(indexes, fields, shape)
        if end_index is not None:
            return False
        if start_index is None and shape == "month":
            return False
    return True


def stamp_work_history(fields: list[dict[str, Any]] | None, entries: list[dict[str, Any]] | None) -> str:
    """Attach per-role month/year values. Empty means do not invent a date.

    A single start month/year with no list still falls through to config.
    Several roles, or a start and an end, skip the job when the list has no entry.
    """
    form = fields or []
    roles = list(entries or [])
    groups = group_work_history_fields(form)
    if not groups:
        return ""
    if not roles:
        if len(groups) == 1 and _single_start_pair(groups[0], form):
            return ""
        for indexes in groups:
            for index in indexes:
                form[index]["_history_assigned"] = True
        return "Unanswered required question: work history"

    for group_index, indexes in enumerate(groups):
        entry = roles[group_index] if group_index < len(roles) else None
        next_start = groups[group_index + 1][0] if group_index + 1 < len(groups) else len(form)
        checkbox = _current_checkbox(form, indexes[0], next_start)
        if entry is None:
            for index in indexes:
                form[index]["_history_assigned"] = True
            return "Unanswered required question: work history"
        current = bool(entry.get("current"))
        if current and checkbox is not None:
            form[checkbox]["_history_value"] = "Yes"
        pairs = (
            ("month", "start_month", "end_month"),
            ("year", "start_year", "end_year"),
        )
        for shape, start_key, end_key in pairs:
            start_index, end_index = _slot_indexes(indexes, form, shape)
            if start_index is not None:
                form[start_index]["_history_assigned"] = True
                value = str(entry.get(start_key) or "").strip()
                if value:
                    form[start_index]["_history_value"] = value
                elif form[start_index].get("required"):
                    label = " ".join(str(form[start_index].get("question") or "").split()) or shape
                    return "Unanswered required question: " + label
            if end_index is None:
                continue
            form[end_index]["_history_assigned"] = True
            if current and checkbox is not None:
                form[end_index]["_history_skip"] = True
                continue
            value = "" if current else str(entry.get(end_key) or "").strip()
            if value:
                form[end_index]["_history_value"] = value
            elif form[end_index].get("required"):
                label = " ".join(str(form[end_index].get("question") or "").split()) or ("end " + shape)
                return "Unanswered required question: " + label
    return ""


def required_field_fingerprint(fields: list[dict[str, Any]] | None) -> str:
    """Hash required control values. Empty when the page has no required fields."""
    parts: list[str] = []
    for field in fields or []:
        if not field.get("required"):
            continue
        parts.append(
            "\t".join(
                [
                    str(field.get("question") or ""),
                    str(field.get("kind") or ""),
                    str(field.get("value") or ""),
                    "1" if field.get("checked") else "0",
                ]
            )
        )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def grounded_control_value(field: dict[str, Any] | None, config_module: Any | None = None) -> str:
    """Config or work-history value for a date or month/year control. Empty means do not invent one."""
    import config as default_config

    info = field or {}
    override = str(info.get("_history_value") or "").strip()
    if override:
        return override
    if info.get("_history_skip"):
        return ""
    cfg = config_module or default_config
    shape = control_shape(info)
    if info.get("_history_assigned") and shape in {"month", "year"}:
        return ""
    if shape == "date" and is_availability_date(info):
        return str(getattr(cfg, "earliest_start_date", "") or "").strip()
    if shape == "month":
        return str(getattr(cfg, "work_history_month", "") or "").strip()
    if shape == "year":
        return str(getattr(cfg, "work_history_year", "") or "").strip()
    return ""


def unanswered_required_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Required screening fields that still have no grounded answer."""
    from linkedin_easy_apply.question_capture import should_skip_field

    leftover: list[dict[str, Any]] = []
    for field in unanswered_fields(fields):
        shape = control_shape(field)
        if should_skip_field(field) and shape not in {"date", "month", "year"}:
            continue
        if field.get("_answered"):
            continue
        if field.get("required"):
            leftover.append(field)
    return leftover


def unanswered_required_reason(fields: list[dict[str, Any]] | None) -> str:
    """Skip the job when a required field still has no grounded answer.

    Sensitive questions are named first. Blank URL, date, and month/year
    controls are included, not only sponsorship-style questions.
    """
    from linkedin_easy_apply.store import is_sensitive_question

    leftover = unanswered_required_fields(fields or [])
    if not leftover:
        return ""
    sensitive = [
        field
        for field in leftover
        if is_sensitive_question(str(field.get("question") or ""))
    ]
    chosen = sensitive[0] if sensitive else leftover[0]
    question = " ".join(str(chosen.get("question") or "").split())
    if not question:
        question = control_shape(chosen) or str(chosen.get("kind") or "field")
    return "Unanswered required question: " + question


def unanswered_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for field in fields:
        if field.get("_history_skip"):
            continue
        kind = str(field.get("kind") or "")
        if kind == "file":
            continue
        value = str(field.get("value") or "").strip()
        shape = control_shape(field)
        if kind == "checkbox" and field.get("checked"):
            continue
        if kind == "radio" and (field.get("checked") or value):
            continue
        if kind == "select" or shape in {"month", "year"}:
            if value and not select_value_is_placeholder(value):
                continue
            pending.append(field)
            continue
        if value and kind in {"text", "textarea", "tel", "email", "number", "date"}:
            continue
        if value and shape == "date":
            continue
        pending.append(field)
    return pending


def match_option(value: str, options: list[str]) -> str:
    wanted = " ".join((value or "").split()).lower()
    cleaned = [str(item).strip() for item in options if str(item).strip()]
    if not wanted or not cleaned:
        return ""
    for option in cleaned:
        if option.lower() == wanted:
            return option
    for option in cleaned:
        if wanted in option.lower() or option.lower() in wanted:
            return option
    if wanted in {"yes", "no"}:
        for option in cleaned:
            if option.lower().startswith(wanted):
                return option
    return ""
