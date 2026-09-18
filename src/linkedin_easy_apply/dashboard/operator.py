"""Operator Settings routes: models, chat, resumes, caps, schedule."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from linkedin_easy_apply.llm import chat_complete, clear_status_cache, list_remote_models
from linkedin_easy_apply.operator_settings import (
    MAX_SNIPPET_CHARS,
    MAX_SUMMARY_CHARS,
    RESUME_MAX_BYTES,
    SETTING_DAY_CAP,
    SETTING_OLLAMA_MODEL,
    SETTING_RESUME_PATH,
    SETTING_RUN_CAP,
    SETTING_SCHEDULE,
    SETTING_SUMMARY,
    is_pdf_bytes,
    normalize_model_name,
    normalize_windows,
    public_operator_settings,
    redact_secrets,
    resolved_ollama_model,
    resolved_resume_path,
    resume_dir,
    safe_resume_name,
    schedule_status,
)
from linkedin_easy_apply.store import Store

log = logging.getLogger(__name__)

CHAT_CONTEXT = 12
OUTSIDE_SCHEDULE = "Outside schedule."


def _settings_payload(store: Store) -> dict[str, Any]:
    return public_operator_settings(store)


def _list_resumes(store: Store, data_dir: str) -> list[dict[str, Any]]:
    folder = resume_dir(data_dir)
    active = resolved_resume_path(store=store)
    active_abs = os.path.abspath(active) if active else ""
    items: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.pdf")):
        if not path.is_file():
            continue
        items.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "active": os.path.abspath(str(path)) == active_abs,
            }
        )
    return items


def _chat_system_prompt(store: Store, attach_resume: bool) -> str:
    from linkedin_easy_apply.facts import extract_resume_text
    from linkedin_easy_apply.operator_settings import resolved_applicant_summary

    summary = redact_secrets(resolved_applicant_summary(store=store))
    resume_excerpt = ""
    if attach_resume:
        resume_excerpt = redact_secrets(extract_resume_text(resolved_resume_path(store=store), limit=2000))
    parts = [
        "You help the operator test LinkedIn Easy Apply answers and improve instructions.",
        "Stay on this machine. Do not invent employers, degrees, or clearances.",
        "Never request or repeat passwords.",
    ]
    if summary:
        parts.append("Applicant summary:\n" + summary)
    if resume_excerpt:
        parts.append("Resume excerpt:\n" + resume_excerpt)
    return "\n".join(parts)


def register_operator_routes(
    app: FastAPI,
    *,
    store: Store,
    worker: Any,
    ollama: Any,
    data_dir: str,
    templates: Jinja2Templates,
    context_fn: Any,
) -> None:
    def models_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "models.html", context_fn(request))

    app.add_api_route("/models", models_page, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/settings", models_page, methods=["GET"], response_class=HTMLResponse)

    @app.get("/api/settings")
    def api_settings() -> dict[str, Any]:
        payload = _settings_payload(store)
        payload["resumes"] = _list_resumes(store, data_dir)
        return payload

    @app.post("/api/settings")
    async def api_settings_save(request: Request) -> dict[str, Any]:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        try:
            if "ollama_model" in body and str(body.get("ollama_model") or "").strip():
                name = normalize_model_name(str(body.get("ollama_model") or ""))
                store.set_setting(SETTING_OLLAMA_MODEL, name)
                if hasattr(ollama, "model"):
                    ollama.model = name
                clear_status_cache()
            if "max_applications_per_day" in body:
                store.set_setting(SETTING_DAY_CAP, str(int(body.get("max_applications_per_day"))))
            if "max_applications_per_run" in body:
                store.set_setting(SETTING_RUN_CAP, str(int(body.get("max_applications_per_run"))))
            if "applicant_summary" in body:
                store.set_setting(
                    SETTING_SUMMARY,
                    redact_secrets(str(body.get("applicant_summary") or ""))[:MAX_SUMMARY_CHARS],
                )
            if "schedule_windows" in body:
                windows = normalize_windows(body.get("schedule_windows"))
                store.set_setting(SETTING_SCHEDULE, json.dumps(windows))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.OperationalError as extra:
            raise HTTPException(status_code=503, detail="Settings store is busy, retry.") from extra
        return _settings_payload(store)

    @app.get("/api/models")
    async def api_models() -> JSONResponse:
        active = resolved_ollama_model(store=store)
        listed = await asyncio.to_thread(list_remote_models, None, 2.0)
        ollama_status = ollama.status() if hasattr(ollama, "status") else {}
        payload = {
            "ok": bool(listed.get("ok")),
            "active": active,
            "models": listed.get("models") or [],
            "host": listed.get("host") or "",
            "ollama": ollama_status,
            "pulling": bool(ollama_status.get("pulling")),
            "pull_model": ollama_status.get("pull_model") or "",
            "pull_progress": ollama_status.get("pull_progress") or "",
        }
        if not listed.get("ok"):
            payload["error"] = listed.get("error") or "Ollama not reachable"
            return JSONResponse(payload, status_code=503)
        return JSONResponse(payload)

    @app.post("/api/models/active")
    async def api_models_active(request: Request) -> dict[str, Any]:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        try:
            name = normalize_model_name(str(body.get("model") or body.get("ollama_model") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.set_setting(SETTING_OLLAMA_MODEL, name)
        if hasattr(ollama, "model"):
            ollama.model = name
        clear_status_cache()
        return {"ok": True, "active": name, "settings": _settings_payload(store)}

    @app.post("/api/models/pull")
    async def api_models_pull(request: Request) -> JSONResponse:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        try:
            name = normalize_model_name(
                str(body.get("model") or body.get("name") or resolved_ollama_model(store=store))
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pull = getattr(ollama, "pull", None)
        if not callable(pull):
            raise HTTPException(status_code=503, detail="Ollama pull is not available")
        result = await asyncio.to_thread(pull, name)
        if not result.get("ok"):
            return JSONResponse(
                {"ok": False, "error": result.get("detail") or "pull failed", "ollama": result},
                status_code=503,
            )
        return JSONResponse({"ok": True, "pulling": True, "model": name, "ollama": result})

    @app.get("/api/models/chat")
    def api_chat_history() -> dict[str, Any]:
        return {
            "messages": store.list_chat_messages(limit=40),
            "model": resolved_ollama_model(store=store),
        }

    @app.post("/api/models/chat")
    async def api_chat(request: Request) -> JSONResponse:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        text = redact_secrets(str(body.get("message") or body.get("content") or "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="message is required")
        if len(text) > 4000:
            raise HTTPException(status_code=400, detail="message is too long")
        attach = bool(body.get("attach_resume"))
        model = resolved_ollama_model(store=store)
        try:
            store.add_chat_message("user", text, model)
        except sqlite3.OperationalError as extra:
            raise HTTPException(status_code=503, detail="Chat store is busy, retry.") from extra
        history = store.list_chat_messages(limit=CHAT_CONTEXT)
        messages = [{"role": "system", "content": _chat_system_prompt(store, attach)}]
        for row in history:
            role = str(row.get("role") or "")
            if role in {"user", "assistant"}:
                messages.append({"role": role, "content": redact_secrets(str(row.get("content") or ""))})
        result = await asyncio.to_thread(chat_complete, messages, model=model, timeout=45.0)
        if not result.get("ok"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": result.get("error") or "Ollama not reachable",
                    "messages": store.list_chat_messages(limit=40),
                },
                status_code=503,
            )
        reply = redact_secrets(str(result.get("message") or "")).strip()
        saved = store.add_chat_message("assistant", reply or "(empty)", model)
        return JSONResponse(
            {
                "ok": True,
                "message": saved,
                "messages": store.list_chat_messages(limit=40),
                "model": model,
            }
        )

    @app.post("/api/models/chat/snippet")
    async def api_chat_snippet(request: Request) -> dict[str, Any]:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        snippet = redact_secrets(str(body.get("text") or body.get("snippet") or "")).strip()
        if not snippet:
            raise HTTPException(status_code=400, detail="snippet is required")
        snippet = snippet[:MAX_SNIPPET_CHARS]
        target = str(body.get("target") or "summary").strip().lower()
        if target == "seed":
            row = store.upsert_question(
                raw_question=snippet,
                field_kind="textarea",
                source="seed",
                proposed_value=snippet,
            )
            return {"ok": True, "target": "seed", "question": row}
        store.set_setting(SETTING_SUMMARY, snippet[:MAX_SUMMARY_CHARS])
        return {"ok": True, "target": "summary", "applicant_summary": snippet[:MAX_SUMMARY_CHARS]}

    @app.get("/api/resumes")
    def api_resumes() -> dict[str, Any]:
        return {
            "resumes": _list_resumes(store, data_dir),
            "active": Path(resolved_resume_path(store=store) or "").name,
        }

    @app.post("/api/resumes")
    async def api_resume_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
        from linkedin_easy_apply.dashboard.app import require_same_origin

        require_same_origin(request)
        filename = safe_resume_name(file.filename or "resume.pdf")
        data = await file.read()
        if len(data) > RESUME_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Resume must be 10MB or smaller")
        if not is_pdf_bytes(data):
            raise HTTPException(status_code=400, detail="Only PDF resumes are accepted")
        folder = resume_dir(data_dir)
        path = folder / filename
        path.write_bytes(data)
        store.set_setting(SETTING_RESUME_PATH, str(path.resolve()))
        return {"ok": True, "name": filename, "resumes": _list_resumes(store, data_dir)}

    @app.post("/api/resumes/active")
    async def api_resume_active(request: Request) -> dict[str, Any]:
        from linkedin_easy_apply.dashboard.app import mutation_payload, require_same_origin

        require_same_origin(request)
        body = await mutation_payload(request)
        name = safe_resume_name(str(body.get("name") or ""))
        path = resume_dir(data_dir) / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Resume not found")
        store.set_setting(SETTING_RESUME_PATH, str(path.resolve()))
        return {"ok": True, "name": name, "resumes": _list_resumes(store, data_dir)}

    @app.delete("/api/resumes/{name}")
    def api_resume_delete(name: str) -> dict[str, Any]:
        filename = safe_resume_name(name)
        path = resume_dir(data_dir) / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Resume not found")
        active = resolved_resume_path(store=store)
        alive = bool(getattr(worker, "status", lambda: {})().get("alive"))
        if alive and active and os.path.abspath(str(path)) == os.path.abspath(active):
            raise HTTPException(
                status_code=409,
                detail="Cannot delete the resume the running apply is using",
            )
        path.unlink()
        if active and os.path.abspath(str(path)) == os.path.abspath(active):
            store.set_setting(SETTING_RESUME_PATH, "")
        return {"ok": True, "resumes": _list_resumes(store, data_dir)}
