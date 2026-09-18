"""FastAPI operator console bound to localhost."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from linkedin_easy_apply.importer import import_text_logs
from linkedin_easy_apply.job_fit import last_fit_status
from linkedin_easy_apply.llm import status as llm_status
from linkedin_easy_apply.metrics import build_metrics
from linkedin_easy_apply.ollama_runtime import OllamaManager
from linkedin_easy_apply.operator_settings import resolve_application_caps, schedule_status
from linkedin_easy_apply.pacing import normalize_pace
from linkedin_easy_apply.quota import applications_today, remaining_applications
from linkedin_easy_apply.runtime import (
    WORKER_RUNNING_LOGIN_ERROR,
    is_profile_locked,
    start_profile_login,
)
from linkedin_easy_apply.scheduler import RunScheduler
from linkedin_easy_apply.store import (
    Store,
    default_store_path,
    grouped_job_counts,
    job_decision,
    question_origin,
)
from linkedin_easy_apply.worker import PROFILE_LOCK_ERROR, WorkerManager

START_CONTRACT = (
    "Start run = Ollama + worker. Login opens the bot Firefox profile at LinkedIn. "
    "With this console open you do not need Start-Bot.cmd or Start-Ollama.cmd."
)

SAFE_ARTIFACT = re.compile(r"^(easy_apply_(failure|page)|job_load)_[A-Za-z0-9._-]+$")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
PUBLIC_VALUE_LIMIT = 400
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
HOST = "127.0.0.1"
PORT = 8787
log = logging.getLogger(__name__)


def project_root() -> str:
    return str(Path(__file__).resolve().parents[3])


def mask_sensitive_text(value: str, limit: int = PUBLIC_VALUE_LIMIT) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\[redacted[-_]?email\]", "[email]", text)
    text = re.sub(r"(?i)\[redacted[-_]?phone\]", "[phone]", text)
    text = EMAIL_RE.sub("[email]", text)
    text = PHONE_RE.sub("[phone]", text)
    compact = " ".join(text.split())
    if len(compact) > limit:
        return compact[:limit].rstrip() + "…"
    return compact


INPUT_ANSWER_KINDS = frozenset({"email", "tel", "text", "number"})


def _is_sensitive_value(value: str) -> bool:
    text = str(value or "")
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        return True
    lowered = text.lower()
    return "[email]" in lowered or "[phone]" in lowered or "redacted" in lowered


def _config_contact_prefill(kind: str) -> str:
    try:
        import config as config_module
    except ImportError:
        return ""
    if kind == "email":
        return str(getattr(config_module, "email", "") or "").strip()
    if kind in {"tel", "phone"}:
        return str(getattr(config_module, "phone_number", "") or "").strip()
    return ""


def public_question(row: dict[str, Any]) -> dict[str, Any]:
    proposed = str(row.get("proposed_value") or "")
    approved = str(row.get("approved_value") or "")
    question_id = str(row.get("id") or row.get("question_id") or "")
    raw = str(row.get("raw_question") or "")
    cleaned = str(row.get("cleaned_question") or row.get("question_text") or raw)
    text = cleaned or raw
    kind = str(
        row.get("classified_kind") or row.get("kind") or row.get("field_kind") or ""
    ).strip().lower()
    status = str(row.get("approval_status") or row.get("status") or "pending")
    title = str(row.get("title") or row.get("last_job_title") or "")
    company = str(row.get("company") or row.get("last_job_company") or "")
    job_id = str(row.get("job_id") or row.get("last_job_id") or "")
    updated = str(row.get("last_seen_at") or row.get("updated_at") or "")
    if kind in INPUT_ANSWER_KINDS:
        options: list[str] = []
    else:
        options = [mask_sensitive_text(str(option)) for option in row.get("options") or []]
    prefill = _config_contact_prefill(kind) if kind in {"email", "tel"} else ""
    if not prefill:
        prefill = "" if _is_sensitive_value(proposed) else proposed
    return {
        "question_id": question_id,
        "question_text": mask_sensitive_text(text),
        "cleaned_question": mask_sensitive_text(cleaned),
        "raw_question": mask_sensitive_text(raw),
        "kind": kind,
        "classified_kind": kind,
        "answer_shape": str(row.get("answer_shape") or ""),
        "options": options,
        "proposed_value": mask_sensitive_text(proposed),
        "approved_value": mask_sensitive_text(approved),
        "prefill": prefill,
        "status": status,
        "source": str(row.get("source") or ""),
        "origin": str(
            row.get("origin")
            or question_origin(str(row.get("source") or ""), str(row.get("provenance") or ""))
        ),
        "seen_count": int(row.get("seen_count") or 0),
        "last_job_id": job_id,
        "last_job_title": mask_sensitive_text(title),
        "last_job_company": mask_sensitive_text(company),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": updated,
    }


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    host = request.headers.get("host") or ""
    if not host:
        return
    allowed = {f"http://{host}", f"https://{host}"}
    if origin and origin not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin form rejected")
    if not origin and referer and not any(
        referer == base or referer.startswith(base + "/") for base in allowed
    ):
        raise HTTPException(status_code=403, detail="Cross-origin form rejected")


async def mutation_payload(request: Request) -> dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    raw = await request.body()
    text = raw.decode("utf-8") if raw else ""
    if "application/json" in content_type:
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        return payload if isinstance(payload, dict) else {}
    parsed = parse_qs(text, keep_blank_values=True)
    return {key: (values[-1] if values else "") for key, values in parsed.items()}


def _llm_status_payload() -> dict[str, Any]:
    try:
        payload = llm_status()
    except (OSError, TimeoutError, ValueError) as exc:
        return {
            "mode": "auto",
            "ready": False,
            "host": "",
            "model": "",
            "detail": "status unavailable: " + exc.__class__.__name__,
        }
    if isinstance(payload, dict):
        return payload
    return {
        "mode": "auto",
        "ready": False,
        "host": "",
        "model": "",
        "detail": "status unavailable",
    }


SEARCH_EVENT_RE = re.compile(
    r"Category:\s*(.+?),\s*Location:\s*(.+?)(?:,\s*Applying|\s*$)",
    re.IGNORECASE,
)


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json") if isinstance(event, dict) else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def search_payload(events: list[dict[str, Any]] | None, config_module: Any) -> dict[str, Any]:
    keywords = [str(item) for item in (getattr(config_module, "keywords", None) or []) if str(item).strip()]
    locations = [str(item) for item in (getattr(config_module, "location", None) or []) if str(item).strip()]
    keyword = ""
    location = ""
    for event in events or []:
        payload = _event_payload(event)
        if str(payload.get("kind") or "") == "search":
            keyword = str(payload.get("keyword") or "").strip()
            location = str(payload.get("location") or "").strip()
            if keyword or location:
                break
        match = SEARCH_EVENT_RE.search(str(event.get("message") or ""))
        if match:
            keyword = match.group(1).strip()
            location = re.sub(r",?\s*Applying.*$", "", match.group(2).strip()).strip()
            break
    return {
        "keywords": keywords,
        "locations": locations,
        "keyword": keyword or (keywords[0] if keywords else ""),
        "location": location or (locations[0] if locations else ""),
    }


def public_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    status = str(row.get("status") or "")
    fit = row.get("fit_score")
    try:
        fit_score = float(fit) if fit is not None and str(fit) != "" else None
    except (TypeError, ValueError):
        fit_score = None
    screenshot = Path(str(row.get("screenshot_path") or "")).name
    html_name = Path(str(row.get("html_path") or "")).name
    return {
        "job_id": str(row.get("job_id") or ""),
        "title": str(row.get("title") or ""),
        "company": str(row.get("company") or ""),
        "location": str(row.get("location_text") or row.get("location") or ""),
        "location_text": str(row.get("location_text") or row.get("location") or ""),
        "sector": str(row.get("sector") or ""),
        "market": str(row.get("market") or ""),
        "outcome": str(row.get("outcome") or ""),
        "url": str(row.get("url") or ""),
        "status": status,
        "decision": job_decision(status),
        "reason": str(row.get("reason") or ""),
        "fit_score": fit_score,
        "seen_at": str(row.get("seen_at") or ""),
        "applied_at": str(row.get("applied_at") or ""),
        "screenshot": screenshot if SAFE_ARTIFACT.match(screenshot) else "",
        "html": html_name if SAFE_ARTIFACT.match(html_name) else "",
        "screenshot_path": str(row.get("screenshot_path") or ""),
    }


def public_failure(row: dict[str, Any] | None) -> dict[str, Any] | None:
    job = public_job(row)
    if job is None:
        return None
    job["reason"] = str(row.get("reason") or job.get("status") or "failed")
    return job


def _empty_metrics_window() -> dict[str, Any]:
    return {
        "since": "",
        "until": "",
        "totals": {"applied": 0, "skipped": 0, "failed": 0, "seen": 0, "total": 0},
        "by_sector": [],
        "by_market": [],
        "by_location": [],
        "by_outcome": [],
    }


def _chart_rows(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        applied = int(item.get("applied") or 0)
        skipped = int(item.get("skipped") or 0)
        failed = int(item.get("failed") or 0)
        seen = int(item.get("seen") or 0)
        total = int(item.get("count") or item.get("total") or 0) or (applied + skipped + failed + seen)
        label = str(item.get("key") or item.get("label") or "other")
        out.append(
            {
                "key": label,
                "label": label,
                "applied": applied,
                "skipped": skipped,
                "failed": failed,
                "seen": seen,
                "total": total,
                "count": total,
            }
        )
    return out[:8]


def _vs_from_window(window: dict[str, Any]) -> dict[str, int]:
    totals = window.get("totals") if isinstance(window.get("totals"), dict) else {}
    vs = {
        "applied": int(totals.get("applied") or 0),
        "skipped": int(totals.get("skipped") or 0),
        "failed": int(totals.get("failed") or 0),
        "seen": int(totals.get("seen") or 0),
    }
    if vs["applied"] or vs["skipped"] or vs["failed"] or vs["seen"]:
        return vs
    for item in window.get("by_outcome") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key in vs:
            vs[key] = int(item.get("count") or 0)
    return vs


def _alias_or_rows(payload: dict[str, Any], alias: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = payload.get(alias)
    if isinstance(existing, list) and existing:
        return _chart_rows(existing)
    return fallback


def charts_from_job_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    today = payload.get("today") if isinstance(payload.get("today"), dict) else {}
    week = payload.get("last_7_days") if isinstance(payload.get("last_7_days"), dict) else {}
    hist = week if week.get("by_sector") or week.get("by_market") or week.get("by_location") else today
    existing_vs = payload.get("applied_vs_skipped") if isinstance(payload.get("applied_vs_skipped"), dict) else {}
    vs = {
        "applied": int(existing_vs.get("applied") or 0),
        "skipped": int(existing_vs.get("skipped") or 0),
        "failed": int(existing_vs.get("failed") or 0),
    }
    if not (vs["applied"] or vs["skipped"] or vs["failed"]):
        vs = _vs_from_window(today)
    return {
        "source": str(payload.get("source") or "store"),
        "applied_vs_skipped": {
            "applied": int(vs.get("applied") or 0),
            "skipped": int(vs.get("skipped") or 0),
            "failed": int(vs.get("failed") or 0),
        },
        "sectors": _alias_or_rows(payload, "sectors", _chart_rows(hist.get("by_sector"))),
        "markets": _alias_or_rows(payload, "markets", _chart_rows(hist.get("by_market"))),
        "locations": _alias_or_rows(payload, "locations", _chart_rows(hist.get("by_location"))),
    }


def empty_metrics() -> dict[str, Any]:
    return {
        "source": "unavailable",
        "today": _empty_metrics_window(),
        "last_7_days": _empty_metrics_window(),
        "taxonomy": {"sectors": [], "markets": [], "outcomes": []},
        "applied_vs_skipped": {"applied": 0, "skipped": 0, "failed": 0},
        "sectors": [],
        "markets": [],
        "locations": [],
    }


_METRICS_ERRORS = (
    OSError,
    TypeError,
    ValueError,
    AttributeError,
    KeyError,
    RuntimeError,
    sqlite3.OperationalError,
)


def derive_metrics(store: Store, source: str = "derived") -> dict[str, Any]:
    try:
        job_metrics = getattr(store, "job_metrics", None)
        if callable(job_metrics):
            payload = job_metrics()
            if isinstance(payload, dict) and "today" in payload:
                merged = dict(payload)
                charts = charts_from_job_metrics(payload)
                charts["source"] = str(payload.get("source") or "store")
                merged.update(charts)
                return merged
        jobs = store.list_jobs(limit=400)
        return build_metrics(jobs, source=source)
    except _METRICS_ERRORS:
        return empty_metrics()


def create_app(
    store: Store | None = None,
    worker: WorkerManager | None = None,
    ollama: OllamaManager | None = None,
    data_dir: str = "data",
    enable_scheduler: bool = False,
) -> FastAPI:
    store = store or Store(default_store_path())
    worker = worker or WorkerManager(store, project_root=project_root())
    ollama = ollama or OllamaManager(project_root())
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app = FastAPI(title="LinkedIn Easy Apply console", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.worker = worker
    app.state.ollama = ollama
    app.state.data_dir = data_dir
    start_gate = threading.Lock()
    scheduler_holder: dict[str, RunScheduler | None] = {"item": None}

    def context(request: Request, **extra: Any) -> dict[str, Any]:
        payload = {"nav": str(request.url.path), "start_contract": START_CONTRACT}
        payload.update(extra)
        return payload

    def fail_start(error: str, *, status_code: int = 409, extra: dict[str, Any] | None = None) -> JSONResponse:
        worker.last_error = error
        payload: dict[str, Any] = {"ok": False, "error": error, "ollama": ollama.status()}
        if extra:
            payload.update(extra)
        return JSONResponse(payload, status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    def live(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "live.html", context(request))

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "applications.html", context(request))

    @app.get("/questions", response_class=HTMLResponse)
    def questions(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "questions.html", context(request))

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "diagnostics.html", context(request))

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        import config

        worker_status = worker.status()
        run = worker_status.get("run") or {}
        run_id = str(run["run_id"]) if run.get("run_id") else ""
        events = store.list_events(run_id or None, limit=80)
        raw_latest = store.latest_job_for_run(run_id) if run_id else None
        latest = public_job(raw_latest)
        last_job = latest or public_job(store.latest_job())
        applied_this_run = store.count_jobs(run_id, "applied") if run_id else 0
        caps = resolve_application_caps(store=store)
        run_cap = int(caps["max_per_run"])
        day_cap = int(caps["max_per_day"])
        applied_today = applications_today()
        remaining = remaining_applications(applied_this_run, run_cap, day_cap, store=store)
        profile = getattr(config, "firefoxProfileRootDir", "")
        locked = bool(profile and is_profile_locked(profile))
        last_error = str(worker_status.get("last_error") or getattr(worker, "last_error", "") or "")
        if locked and not worker_status.get("alive") and not last_error:
            last_error = PROFILE_LOCK_ERROR
        llm = _llm_status_payload()
        from linkedin_easy_apply.operator_settings import resolved_ollama_model

        llm["model"] = resolved_ollama_model(store=store)
        ollama_payload = dict(ollama.status())
        ollama_payload.setdefault("tokens", llm.get("tokens") or llm.get("context_length"))
        ollama_payload["model"] = llm["model"]
        schedule = schedule_status(store)
        counts = store.job_counts()
        last_fit = last_fit_status(events, raw_latest or (last_job or None))
        try:
            metrics = derive_metrics(store)
        except (OSError, TypeError, ValueError, AttributeError, KeyError, RuntimeError, sqlite3.OperationalError):
            metrics = empty_metrics()
        return {
            "worker": worker_status,
            "current_job": latest,
            "last_job": last_job,
            "last_decision": (last_job or {}).get("decision") if last_job else "",
            "last_fit": last_fit,
            "search": search_payload(events, config),
            "counts": counts,
            "groups": grouped_job_counts(counts),
            "pace": normalize_pace(getattr(config, "pace", "human")),
            "quota": {
                "applied_today": applied_today,
                "applied_this_run": applied_this_run,
                "remaining": remaining,
                "run_remaining": max(0, run_cap - applied_this_run),
                "day_remaining": max(0, day_cap - applied_today),
                "run_cap": run_cap,
                "day_cap": day_cap,
            },
            "schedule": schedule,
            "profile_locked": locked,
            "events": events,
            "llm": llm,
            "llm_ready": bool(llm.get("ready")),
            "ollama": ollama_payload,
            "questions": store.question_counts(),
            "metrics": metrics,
            "last_error": last_error,
            "start_contract": START_CONTRACT,
            "hotkeys": {"start": "s", "stop": "x", "approve": "a"},
        }

    @app.get("/api/metrics")
    def api_metrics() -> dict[str, Any]:
        try:
            payload = derive_metrics(store)
        except _METRICS_ERRORS:
            payload = empty_metrics()
        try:
            payload["groups"] = grouped_job_counts(store.job_counts())
        except _METRICS_ERRORS:
            payload["groups"] = grouped_job_counts({})
        return payload

    @app.get("/api/jobs")
    def api_jobs(
        status: str = Query(default=""),
        q: str = Query(default=""),
    ) -> dict[str, Any]:
        jobs = [item for item in (public_job(row) for row in store.list_jobs(status=status, query=q, limit=300)) if item]
        counts = store.job_counts()
        try:
            metrics = derive_metrics(store)
        except _METRICS_ERRORS:
            metrics = empty_metrics()
        return {
            "jobs": jobs,
            "counts": counts,
            "groups": grouped_job_counts(counts),
            "metrics": metrics,
        }

    @app.get("/api/artifacts")
    def api_artifacts() -> dict[str, Any]:
        folder = Path(data_dir)
        items = []
        if folder.is_dir():
            for path in sorted(
                list(folder.glob("easy_apply_*")) + list(folder.glob("job_load_*")),
                reverse=True,
            ):
                if SAFE_ARTIFACT.match(path.name):
                    items.append(
                        {
                            "name": path.name,
                            "kind": "image" if path.suffix.lower() == ".png" else "html",
                            "size": path.stat().st_size,
                        }
                    )
        return {
            "latest_failure": public_failure(store.latest_failure()),
            "artifacts": items[:40],
        }

    @app.get("/artifacts/{name}")
    def artifact(name: str):
        if not SAFE_ARTIFACT.match(name):
            raise HTTPException(status_code=400, detail="Invalid artifact name")
        path = Path(data_dir) / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        media = "image/png" if path.suffix.lower() == ".png" else "text/html"
        return FileResponse(path, media_type=media)

    def start_run_job(*, scheduled: bool = False) -> JSONResponse:
        import config
        from linkedin_easy_apply.dashboard.operator import OUTSIDE_SCHEDULE

        if not start_gate.acquire(blocking=False):
            return fail_start("Start is already in progress.")
        try:
            sched = schedule_status(store)
            if sched.get("enabled") and not sched.get("active") and not scheduled:
                return fail_start(OUTSIDE_SCHEDULE, extra={"schedule": sched})
            current = worker.status()
            if current.get("alive"):
                return fail_start(
                    "A worker is already running.",
                    extra={"pid": current.get("pid")},
                )
            profile = getattr(config, "firefoxProfileRootDir", "")
            if profile and is_profile_locked(profile):
                return fail_start(PROFILE_LOCK_ERROR)
            ollama_result = ollama.ensure()
            result = worker.start(profile)
            result["ollama"] = ollama_result
            ollama_detail = str(ollama_result.get("detail") or "")
            if ollama_result.get("pulling"):
                result["message"] = ollama_detail or "pulling model"
            elif not ollama_result.get("ok"):
                result["warning"] = ollama_detail or "Ollama is not reachable."
            elif not ollama_result.get("ready"):
                result["warning"] = ollama_detail
            if not result.get("ok"):
                error = str(result.get("error") or "Start failed")
                if not ollama_result.get("ok") and ollama_detail and ollama_detail.lower() not in error.lower():
                    error = error + " Ollama: " + ollama_detail
                result["error"] = error
                worker.last_error = error
                return JSONResponse(result, status_code=409)
            worker.last_error = ""
            return JSONResponse(result, status_code=200)
        finally:
            start_gate.release()

    @app.post("/api/run/start")
    def api_start() -> JSONResponse:
        return start_run_job(scheduled=False)

    @app.post("/api/run/stop")
    def api_stop() -> dict[str, Any]:
        return worker.stop()

    @app.post("/api/ollama/stop")
    def api_ollama_stop() -> dict[str, Any]:
        return ollama.stop()

    @app.post("/api/login")
    def api_login(request: Request) -> JSONResponse:
        require_same_origin(request)
        import config

        if not start_gate.acquire(blocking=False):
            return fail_start("Start is already in progress. Stop the worker before LinkedIn login.")
        try:
            current = worker.status()
            if current.get("alive"):
                return fail_start(WORKER_RUNNING_LOGIN_ERROR)
            result = start_profile_login(getattr(config, "firefoxProfileRootDir", ""))
            if not result.get("ok"):
                error = str(result.get("error") or "Login failed")
                worker.last_error = error
                return JSONResponse({"ok": False, "error": error}, status_code=409)
            worker.last_error = ""
            return JSONResponse(result, status_code=200)
        finally:
            start_gate.release()

    @app.get("/api/questions")
    def api_questions(
        status: str = Query(default=""),
        q: str = Query(default=""),
        kind: str = Query(default=""),
        source: str = Query(default=""),
    ) -> dict[str, Any]:
        from linkedin_easy_apply.question_classifier import repair_contact_classifications

        try:
            repair_contact_classifications(store)
        except sqlite3.OperationalError as extra:
            log.warning("questions list repair sqlite: %s", extra.__class__.__name__)
        questions = store.list_questions(status=status, query=q, kind=kind, source=source, limit=300)
        return {
            "questions": [public_question(item) for item in questions],
            "counts": store.question_counts(),
            "origins": store.question_origin_counts(),
            "kinds": store.question_kinds(),
        }

    @app.post("/api/questions/classify-pending")
    async def api_questions_classify_pending(request: Request) -> dict[str, Any]:
        require_same_origin(request)
        from linkedin_easy_apply.question_classifier import classify_pending_questions

        try:
            result = await asyncio.to_thread(classify_pending_questions, store)
        except sqlite3.OperationalError as extra:
            log.warning("classify-pending sqlite: %s", extra.__class__.__name__)
            raise HTTPException(status_code=503, detail="Question store is busy, retry.") from extra
        return result

    @app.post("/api/questions/generate-seeds")
    async def api_questions_generate_seeds(request: Request) -> dict[str, Any]:
        require_same_origin(request)
        from linkedin_easy_apply.question_generator import generate_seed_questions

        try:
            result = await asyncio.to_thread(generate_seed_questions, store)
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail="Question store is busy, retry.") from exc
        return {
            "ok": True,
            "inserted": int(result.get("inserted") or 0),
            "skipped": int(result.get("skipped") or 0),
            "candidates": int(result.get("candidates") or 0),
            "source": str(result.get("source") or "manual"),
            "capped": bool(result.get("capped")),
            "counts": store.question_counts(),
        }

    @app.post("/api/questions/approve")
    async def api_questions_approve(request: Request) -> dict[str, Any]:
        require_same_origin(request)
        payload = await mutation_payload(request)
        question_id = str(payload.get("question_id") or "")
        approved_value = str(payload.get("approved_value") or "")
        try:
            row = await asyncio.to_thread(store.approve_question, question_id, approved_value)
        except sqlite3.OperationalError as extra:
            log.warning("approve sqlite: %s", extra.__class__.__name__)
            raise HTTPException(status_code=503, detail="Question store is busy, retry.") from extra
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"question": public_question(row), "counts": store.question_counts()}

    @app.post("/api/questions/reject")
    async def api_questions_reject(request: Request) -> dict[str, Any]:
        require_same_origin(request)
        payload = await mutation_payload(request)
        question_id = str(payload.get("question_id") or "")
        try:
            row = await asyncio.to_thread(store.reject_question, question_id)
        except sqlite3.OperationalError as extra:
            log.warning("reject sqlite: %s", extra.__class__.__name__)
            raise HTTPException(status_code=503, detail="Question store is busy, retry.") from extra
        if row is None:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"question": public_question(row), "counts": store.question_counts()}

    from linkedin_easy_apply.dashboard.operator import register_operator_routes

    register_operator_routes(
        app,
        store=store,
        worker=worker,
        ollama=ollama,
        data_dir=data_dir,
        templates=templates,
        context_fn=context,
    )

    def _scheduler_start() -> dict[str, Any]:
        response = start_run_job(scheduled=True)
        payload = getattr(response, "body", b"{}")
        try:
            parsed = json.loads(payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            parsed = {"ok": getattr(response, "status_code", 500) < 400}
        if not isinstance(parsed, dict):
            parsed = {"ok": False, "error": "scheduled start failed"}
        parsed.setdefault("ok", getattr(response, "status_code", 500) < 400)
        return parsed

    scheduler = RunScheduler(
        store,
        start_run=_scheduler_start,
        stop_run=worker.stop,
        is_alive=lambda: bool(worker.status().get("alive")),
    )
    app.state.scheduler = scheduler
    scheduler_holder["item"] = scheduler
    if enable_scheduler:
        scheduler.start()

    return app


def run_dashboard() -> int:
    import uvicorn

    os.chdir(project_root())
    store = Store(default_store_path())
    imported = import_text_logs(store)
    if imported:
        print("Imported " + str(imported) + " rows from daily text logs.")
    app = create_app(store=store, enable_scheduler=True)
    thread = threading.Thread(
        target=lambda: (time.sleep(1.2), webbrowser.open(f"http://{HOST}:{PORT}")),
        daemon=True,
    )
    thread.start()
    print(f"Dashboard: http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0
