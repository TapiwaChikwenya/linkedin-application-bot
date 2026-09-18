"""Local Ollama sidecar for reading Easy Apply questions and answering from facts."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2"
_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STATUS_TTL_READY_SEC = 20.0
_STATUS_TTL_NOT_READY_SEC = 2.0


def llm_mode(config_module: Any | None = None) -> str:
    import config as default_config

    cfg = config_module or default_config
    raw = str(getattr(cfg, "llm_mode", os.getenv("LINKEDIN_LLM", "auto")) or "auto")
    mode = raw.strip().lower()
    if mode in {"off", "none", "false", "0"}:
        return "off"
    if mode in {"ollama", "on", "true", "1"}:
        return "ollama"
    return "auto"


def ollama_host(config_module: Any | None = None) -> str:
    import config as default_config

    cfg = config_module or default_config
    return str(
        getattr(cfg, "ollama_host", os.getenv("OLLAMA_HOST", DEFAULT_HOST)) or DEFAULT_HOST
    ).rstrip("/")


def ollama_model(config_module: Any | None = None) -> str:
    from linkedin_easy_apply.operator_settings import resolved_ollama_model

    return resolved_ollama_model(config_module=config_module)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def usable_answer(value: str) -> bool:
    text = " ".join((value or "").split())
    return bool(text) and len(text) <= 400


def status(config_module: Any | None = None) -> dict[str, Any]:
    mode = llm_mode(config_module)
    host = ollama_host(config_module)
    model = ollama_model(config_module)
    if mode == "off":
        return {
            "mode": mode,
            "ready": False,
            "host": host,
            "model": model,
            "detail": "disabled",
        }
    cache_key = mode + "|" + host + "|" + model
    now = time.time()
    cached = _STATUS_CACHE.get(cache_key)
    if cached:
        ttl = _STATUS_TTL_READY_SEC if cached[1].get("ready") else _STATUS_TTL_NOT_READY_SEC
        if now - cached[0] < ttl:
            return cached[1]
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=0.6) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        names = [str(item.get("name") or "") for item in payload.get("models") or []]
        present = any(name.split(":")[0] == model.split(":")[0] for name in names) if names else False
        result = {
            "mode": mode,
            "ready": present,
            "host": host,
            "model": model,
            "detail": "model present" if present else "Ollama is up; pull " + model,
            "models": names[:12],
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        result = {
            "mode": mode,
            "ready": False,
            "host": host,
            "model": model,
            "detail": "Ollama not reachable: " + str(exc.__class__.__name__),
        }
    _STATUS_CACHE[cache_key] = (now, result)
    return result


def clear_status_cache() -> None:
    """Drop cached Ollama probes so the dashboard recovers immediately after Start."""
    _STATUS_CACHE.clear()


def _compact_prompt_answers(rows: list[Any] | None, limit: int = 12) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or row.get("id") or row.get("question_id") or "")
        question = str(row.get("question") or row.get("raw_question") or row.get("question_text") or "")
        value = str(row.get("value") or row.get("approved_value") or "")
        if not source_id or not question.strip() or not value.strip():
            continue
        item = {
            "source_id": source_id,
            "question": " ".join(question.split()),
            "value": " ".join(value.split()),
        }
        match = str(row.get("match") or "")
        if match:
            item["match"] = match
        compact.append(item)
        if len(compact) >= limit:
            break
    return compact


def _retrieve_approved(store: Any | None, questions: list[dict[str, Any]]) -> list[dict[str, str]]:
    if store is None:
        return []
    retrieve = getattr(store, "retrieve_approved_answers", None)
    if not callable(retrieve):
        return []
    try:
        rows = retrieve(questions) or []
    except (OSError, TypeError, ValueError):
        return []
    return _compact_prompt_answers(list(rows))


def build_prompt(
    facts: dict[str, Any],
    snapshot: str,
    questions: list[dict[str, Any]],
    approved_answers: list[dict[str, Any]] | None = None,
) -> str:
    slim_facts = dict(facts)
    yes_no = slim_facts.get("yes_no_rules") or []
    slim_facts["yes_no_rules"] = yes_no[:40]
    resume_text = str(slim_facts.get("resume_text") or "")
    slim_facts["resume_text"] = resume_text[:4000]
    retrieved = _compact_prompt_answers(approved_answers)
    if not retrieved:
        retrieved = _compact_prompt_answers(slim_facts.get("approved_answers"))
    slim_facts["approved_answers"] = retrieved
    identified_questions = [
        {
            "question_id": f"q{index}",
            "question": str(question.get("question") or ""),
            "kind": str(question.get("kind") or ""),
            "options": list(question.get("options") or []),
            "required": bool(question.get("required")),
        }
        for index, question in enumerate(questions[:20])
    ]
    payload = {
        "applicant_facts": slim_facts,
        "visible_form_text": (snapshot or "")[:4000],
        "unanswered_questions": identified_questions,
        "approved_answers": retrieved,
    }
    return (
        "You fill LinkedIn Easy Apply form fields for this applicant.\n"
        "Use approved_answers when present. Copy the approved value for a matching question.\n"
        "If an approved answer conflicts with applicant_facts, omit that question.\n"
        "Use only applicant_facts, approved_answers, and the visible form text. Do not invent "
        "employers, degrees, clearances, or essay answers that are not in the facts or "
        "approved list.\n"
        "If a question cannot be answered from those facts or approved_answers, omit it.\n"
        "Answer only entries in unanswered_questions. Never create or rephrase a question.\n"
        "For every answer, copy its question_id exactly. Include each question_id at most once.\n"
        "Prefer short truthful values. Yes/No must be exactly Yes or No.\n"
        "For select and radio fields, value must exactly match one supplied option.\n"
        "For number fields, value must contain only a number, not a sentence.\n"
        "Years of experience must be an integer from years_experience, approved_answers, "
        "or the resume.\n"
        "Return confidence between 0 and 1 and source_ids of approved_answers you used "
        "(empty list if none).\n"
        "Return JSON only with this shape:\n"
        '{"answers":[{"question_id":"q0","value":"...","confidence":0.0,"source_ids":["12"]}]}\n\n'
        + json.dumps(payload, ensure_ascii=True)
    )


def _validated_value(value: str, question: dict[str, Any]) -> str:
    """Return a field-safe value, or an empty string when the model violated the contract."""
    text = " ".join((value or "").split())
    if not usable_answer(text):
        return ""
    kind = str(question.get("kind") or "").lower()
    if kind == "number" and not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return ""
    if kind == "checkbox":
        return text.title() if text.lower() in {"yes", "no"} else ""
    if kind in {"select", "radio"}:
        options = [str(option).strip() for option in question.get("options") or []]
        for option in options:
            if option and option.casefold() == text.casefold():
                return option
        return ""
    return text


def _optional_confidence(item: dict[str, Any]) -> float | None:
    raw = item.get("confidence")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= value <= 1.0:
        return value
    return None


def _optional_source_ids(item: dict[str, Any]) -> list[str]:
    raw = item.get("source_ids") if "source_ids" in item else item.get("sourceIds")
    if not isinstance(raw, list):
        return []
    return [str(value).strip() for value in raw if str(value).strip()][:8]


def normalize_answers(
    payload: dict[str, Any],
    questions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, list):
        return []
    by_id = {f"q{index}": question for index, question in enumerate((questions or [])[:20])}
    by_text = {
        " ".join(str(question.get("question") or "").casefold().split()): question
        for question in (questions or [])[:20]
        if str(question.get("question") or "").strip()
    }
    seen: set[int] = set()
    cleaned: list[dict[str, Any]] = []
    for item in answers:
        if not isinstance(item, dict):
            continue
        requested: dict[str, Any] | None = None
        if questions is not None:
            question_id = str(item.get("question_id") or "")
            requested = by_id.get(question_id)
            if requested is None:
                generated_question = " ".join(
                    str(item.get("question") or "").casefold().split()
                )
                requested = by_text.get(generated_question)
            if requested is None or id(requested) in seen:
                continue
            question = " ".join(str(requested.get("question") or "").split())
            value = _validated_value(str(item.get("value") or ""), requested)
        else:
            question = " ".join(str(item.get("question") or "").split())
            value = " ".join(str(item.get("value") or "").split())
        if not question or not value:
            continue
        if requested is not None:
            seen.add(id(requested))
        entry: dict[str, Any] = {"question": question, "value": value}
        confidence = _optional_confidence(item)
        if confidence is not None:
            entry["confidence"] = confidence
        source_ids = _optional_source_ids(item)
        if source_ids:
            entry["source_ids"] = source_ids
        cleaned.append(entry)
    return cleaned


ANSWER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["question_id", "value"],
            },
        }
    },
    "required": ["answers"],
}


class OllamaClient:
    def __init__(self, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL, timeout: int = 90):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(
        self,
        prompt: str,
        images: list[str] | None = None,
        response_format: dict[str, Any] | None = None,
        num_predict: int = 700,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": response_format if response_format is not None else ANSWER_RESPONSE_FORMAT,
            "options": {"temperature": 0, "num_predict": int(num_predict)},
        }
        if images:
            body["images"] = images
        request = urllib.request.Request(
            self.host + "/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return str(payload.get("response") or "")


def list_remote_models(host: str | None = None, timeout: float = 2.0) -> dict[str, Any]:
    """Short probe of GET /api/tags. Does not share a hung generate() call."""
    target = (host or ollama_host()).rstrip("/")
    try:
        with urllib.request.urlopen(target + "/api/tags", timeout=max(0.4, float(timeout))) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        names = [str(item.get("name") or "") for item in payload.get("models") or [] if item]
        return {"ok": True, "host": target, "models": names}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return {
            "ok": False,
            "host": target,
            "models": [],
            "error": "Ollama not reachable: " + exc.__class__.__name__,
        }


def chat_complete(
    messages: list[dict[str, str]],
    *,
    host: str | None = None,
    model: str | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """One-shot /api/chat with its own timeout so the dashboard stays responsive."""
    target = (host or ollama_host()).rstrip("/")
    chosen = (model or ollama_model()).strip() or DEFAULT_MODEL
    body = {
        "model": chosen,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 400},
    }
    request = urllib.request.Request(
        target + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout))) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "error": "Ollama not reachable: " + exc.__class__.__name__,
            "model": chosen,
        }
    message = payload.get("message") if isinstance(payload, dict) else None
    content = ""
    if isinstance(message, dict):
        content = str(message.get("content") or "")
    if not content:
        content = str(payload.get("response") or "")
    return {"ok": True, "model": chosen, "message": content, "role": "assistant"}

    def answer_questions(
        self,
        facts: dict[str, Any],
        snapshot: str,
        questions: list[dict[str, Any]],
        images: list[str] | None = None,
        approved_answers: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not questions:
            return []
        raw = self.generate(
            build_prompt(facts, snapshot, questions, approved_answers=approved_answers),
            images=images,
        )
        return normalize_answers(parse_json_object(raw), questions)


def answer_unanswered(
    facts: dict[str, Any],
    snapshot: str,
    questions: list[dict[str, Any]],
    images: list[str] | None = None,
    config_module: Any | None = None,
    store: Any | None = None,
) -> list[dict[str, Any]]:
    info = status(config_module)
    if not info.get("ready"):
        return []
    approved = _retrieve_approved(store, questions)
    bundled = dict(facts)
    if approved:
        bundled["approved_answers"] = approved
    try:
        client = OllamaClient(host=str(info["host"]), model=str(info["model"]))
        return client.answer_questions(
            bundled,
            snapshot,
            questions,
            images=images,
            approved_answers=approved,
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return []


def generate_json(
    prompt: str,
    response_format: dict[str, Any],
    *,
    config_module: Any | None = None,
    timeout: int = 25,
    num_predict: int = 160,
) -> dict[str, Any]:
    """JSON-schema generate. Empty dict when Ollama is down so callers never block a run."""
    info = status(config_module)
    if not info.get("ready"):
        return {}
    try:
        client = OllamaClient(
            host=str(info["host"]),
            model=str(info["model"]),
            timeout=int(timeout),
        )
        raw = client.generate(
            prompt,
            response_format=response_format,
            num_predict=int(num_predict),
        )
        return parse_json_object(raw)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return {}
