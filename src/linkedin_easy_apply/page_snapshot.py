"""Read LinkedIn Easy Apply fields through light DOM and shadow roots."""

from __future__ import annotations

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
  const required = !!(
    el.required ||
    el.getAttribute("aria-required") === "true" ||
    /required/.test(question.toLowerCase())
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


def unanswered_required_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Required screening fields that still have no grounded answer."""
    from linkedin_easy_apply.question_capture import should_skip_field

    leftover: list[dict[str, Any]] = []
    for field in unanswered_fields(fields):
        if should_skip_field(field):
            continue
        if field.get("_answered"):
            continue
        if field.get("required"):
            leftover.append(field)
    return leftover


def unanswered_required_reason(fields: list[dict[str, Any]] | None) -> str:
    """Skip the job when a required *sensitive* field still has no grounded answer."""
    from linkedin_easy_apply.store import is_sensitive_question

    leftover = unanswered_required_fields(fields or [])
    sensitive = [
        field
        for field in leftover
        if is_sensitive_question(str(field.get("question") or ""))
    ]
    if not sensitive:
        return ""
    question = " ".join(str(sensitive[0].get("question") or "").split())
    if not question:
        question = str(sensitive[0].get("kind") or "field")
    return "Unanswered required question: " + question


def unanswered_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for field in fields:
        kind = str(field.get("kind") or "")
        if kind == "file":
            continue
        value = str(field.get("value") or "").strip()
        if kind in {"text", "textarea", "tel", "email", "number"} and value:
            continue
        if kind == "checkbox" and field.get("checked"):
            continue
        if kind == "radio" and (field.get("checked") or value):
            continue
        if kind == "select":
            current = value.lower()
            if current and current not in {"", "select an option", "select"}:
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
