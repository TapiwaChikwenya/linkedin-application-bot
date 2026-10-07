"""Regression coverage for Ollama picker, chat, resume upload, and operator settings.

Uses TestClient plus mocked Ollama HTTP/subprocess. Does not start the LinkedIn
worker and does not pull real models.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from linkedin_easy_apply.dashboard.app import create_app
from linkedin_easy_apply.facts import applicant_facts
from linkedin_easy_apply.llm import clear_status_cache, ollama_model
from linkedin_easy_apply.llm import status as llm_status
from linkedin_easy_apply.ollama_runtime import OllamaManager
from linkedin_easy_apply.operator_settings import (
    RESUME_MAX_BYTES,
    apply_operator_overrides,
    resolved_ollama_model,
    resolved_resume_path,
)
from linkedin_easy_apply.store import Store
from linkedin_easy_apply.worker import WorkerManager

pytestmark = pytest.mark.regression

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
REPO_ROOT = Path(__file__).resolve().parents[1]


class StubOllama:
    def __init__(self, payload=None):
        self.ensure_calls = 0
        self.stop_calls = 0
        self.pull_calls = []
        self.model = "llama3.2"
        self.payload = payload or {
            "ok": True,
            "ready": True,
            "host": "http://127.0.0.1:11434",
            "model": "llama3.2",
            "detail": "model present",
            "started": False,
            "pulling": False,
            "pid": None,
        }

    def ensure(self, wait_seconds=30.0):
        self.ensure_calls += 1
        return dict(self.payload)

    def status(self):
        body = dict(self.payload)
        body["model"] = self.model
        return body

    def stop(self):
        self.stop_calls += 1
        return {"ok": True, "message": "Ollama was not started by this dashboard."}

    def pull(self, model):
        self.pull_calls.append(model)
        self.model = model
        self.payload = dict(self.payload)
        self.payload["ok"] = True
        self.payload["pulling"] = True
        self.payload["pull_model"] = model
        self.payload["detail"] = "pulling model " + model
        return dict(self.payload)


class _FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid
        self._code = None

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        raise AssertionError("ollama pull must not wait on the subprocess")

    def communicate(self, *args, **kwargs):
        raise AssertionError("ollama pull must not block on communicate")


class _FakeHttp:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _harness(tmp_path, ollama=None):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = Store(str(tmp_path / "assistant.sqlite"))
    worker = WorkerManager(store, str(tmp_path))
    sidecar = ollama if ollama is not None else StubOllama()
    client = TestClient(
        create_app(store=store, worker=worker, ollama=sidecar, data_dir=str(data_dir))
    )
    return client, store, worker, sidecar, data_dir


def _client(tmp_path):
    client, store, _worker, _ollama, data_dir = _harness(tmp_path)
    return client, store, data_dir


def _url_of(request) -> str:
    if isinstance(request, str):
        return request
    return str(getattr(request, "full_url", "") or request)


def test_models_page_and_settings_alias_render(tmp_path):
    client, _store, _data_dir = _client(tmp_path)
    page = client.get("/models")
    assert page.status_code == 200
    assert "Ollama models" in page.text
    assert "Resume PDF" in page.text
    assert "Model chat" in page.text
    assert 'id="modelSelect"' in page.text
    assert 'id="pullBtn"' in page.text
    assert 'id="uploadResumeBtn"' in page.text
    assert "data/resumes" in page.text
    assert "starting pull…" in page.text
    assert 'api("POST", "/api/models/pull"' in page.text
    assert "watch Live ticker" in page.text
    assert "Passwords are never sent" in page.text
    assert client.get("/settings").status_code == 200


def test_list_models_from_mocked_api_tags(tmp_path, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=0):
        url = _url_of(request)
        calls.append(url)
        assert url.endswith("/api/tags")
        return _FakeHttp(
            {"models": [{"name": "llama3.2:latest"}, {"name": "tinyllama:latest"}]}
        )

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    client, store, _data_dir = _client(tmp_path)
    listed = client.get("/api/models")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["models"] == ["llama3.2:latest", "tinyllama:latest"]
    assert body["active"] == resolved_ollama_model(store=store)
    assert any(url.endswith("/api/tags") for url in calls)


def test_select_active_model_persists_and_llm_status_reads_override(tmp_path, monkeypatch):
    client, store, _data_dir = _client(tmp_path)
    monkeypatch.setenv("LINKEDIN_STORE_PATH", store.path)
    saved = client.post("/api/models/active", json={"model": "llama3.1"})
    assert saved.status_code == 200
    assert saved.json()["active"] == "llama3.1"
    assert store.get_setting("ollama_model") == "llama3.1"
    assert resolved_ollama_model(store=store) == "llama3.1"

    clear_status_cache()
    assert ollama_model() == "llama3.1"

    def fake_urlopen(request, timeout=0):
        url = _url_of(request)
        assert url.endswith("/api/tags")
        return _FakeHttp({"models": [{"name": "llama3.1:latest"}]})

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    info = llm_status()
    assert info["model"] == "llama3.1"
    assert info["ready"] is True
    status = client.get("/api/status").json()
    assert status["llm"]["model"] == "llama3.1"
    assert status["ollama"]["model"] == "llama3.1"


def test_invalid_model_name_is_rejected(tmp_path):
    client, _store, _data_dir = _client(tmp_path)
    response = client.post("/api/models/active", json={"model": "../evil;rm"})
    assert response.status_code == 400


def test_pull_model_is_background_mocked_subprocess_and_updates_list(tmp_path, monkeypatch):
    launched = []
    tags = {"models": ["llama3.2:latest"]}

    def popen(command, **_kwargs):
        launched.append(command)
        return _FakeProc(pid=99)

    manager = OllamaManager(
        str(tmp_path),
        host="http://127.0.0.1:11434",
        model="llama3.2",
        probe=lambda _host, _timeout=1.0: {"reachable": True, "models": list(tags["models"]), "error": ""},
        popen=popen,
        which=lambda: "/usr/bin/ollama",
        sleep=lambda _dt: None,
        clock=lambda: 0.0,
    )
    client, _store, _worker, _sidecar, _data_dir = _harness(tmp_path, ollama=manager)

    def fake_urlopen(request, timeout=0):
        return _FakeHttp({"models": [{"name": name} for name in tags["models"]]})

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)

    before = client.get("/api/models")
    assert before.status_code == 200
    assert "tinyllama:latest" not in before.json()["models"]

    started = time.monotonic()
    pulled = client.post("/api/models/pull", json={"model": "tinyllama"})
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert pulled.status_code == 200
    payload = pulled.json()
    assert payload["ok"] is True
    assert payload["pulling"] is True
    assert payload["model"] == "tinyllama"
    assert launched
    assert launched[0][-2:] == ["pull", "tinyllama"]

    listed_while_pulling = client.get("/api/models")
    assert listed_while_pulling.status_code == 200
    assert listed_while_pulling.json()["pulling"] is True

    tags["models"] = ["llama3.2:latest", "tinyllama:latest"]
    after = client.get("/api/models")
    assert after.status_code == 200
    assert "tinyllama:latest" in after.json()["models"]


def test_chat_mocked_generate_omits_password_and_returns_503_when_down(tmp_path, monkeypatch):
    import config

    captured = {}
    monkeypatch.setattr(config, "password", "s3cret-pass")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "s3cret-pass")

    def fake_urlopen(request, timeout=0):
        url = _url_of(request)
        if url.endswith("/api/chat"):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHttp({"message": {"role": "assistant", "content": "Yes"}})
        raise AssertionError("unexpected url " + url)

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    client, store, _data_dir = _client(tmp_path)
    chat = client.post(
        "/api/models/chat",
        json={"message": "Authorized to work? password s3cret-pass"},
    )
    assert chat.status_code == 200
    body = chat.json()
    assert body["ok"] is True
    assert body["message"]["content"] == "Yes"
    blob = json.dumps(captured["body"])
    assert "s3cret-pass" not in blob
    assert "[redacted]" in blob
    history = json.dumps(store.list_chat_messages())
    assert "s3cret-pass" not in history
    assert "[redacted]" in history
    assert captured["body"]["stream"] is False
    assert captured["body"]["model"]

    monkeypatch.setattr(
        "linkedin_easy_apply.dashboard.operator.chat_complete",
        lambda messages, **kwargs: {"ok": False, "error": "Ollama not reachable: URLError"},
    )
    down = client.post("/api/models/chat", json={"message": "hello again"})
    assert down.status_code == 503
    assert "Ollama" in down.json()["error"]


def test_models_list_returns_503_when_tags_fail(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise TimeoutError("ollama down")

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", boom)
    client, _store, _data_dir = _client(tmp_path)
    listed = client.get("/api/models")
    assert listed.status_code == 503
    assert listed.json()["ok"] is False


def test_resume_upload_pdf_only_under_data_resumes_and_used_by_facts(tmp_path):
    client, store, data_dir = _client(tmp_path)
    uploaded = client.post(
        "/api/resumes",
        files={"file": ("Tapiwa_Resume.pdf", PDF_BYTES, "application/pdf")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["name"] == "Tapiwa_Resume.pdf"
    saved = data_dir / "resumes" / "Tapiwa_Resume.pdf"
    assert saved.is_file()
    assert saved.read_bytes() == PDF_BYTES
    active = resolved_resume_path(store=store)
    assert Path(active) == saved.resolve()
    listed = client.get("/api/resumes").json()
    assert listed["active"] == "Tapiwa_Resume.pdf"
    assert listed["resumes"][0]["active"] is True

    cfg = SimpleNamespace(
        ollama_model="llama3.2",
        resume_path="",
        max_applications_per_run=12,
        max_applications_per_day=25,
        FirstName="Tapiwa",
        LastName="Tester",
        email="me@example.com",
        phone_number="",
        application_city="Dallas",
        LinkedInProfileURL="",
        country_code="US",
        years_experience={},
        yes_no_answers=[],
    )
    apply_operator_overrides(cfg, store)
    assert Path(cfg.resume_path) == saved.resolve()
    facts = applicant_facts(config_module=cfg, store=store)
    assert facts["resume_filename"] == "Tapiwa_Resume.pdf"

    txt = client.post(
        "/api/resumes",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert txt.status_code == 400
    exe = client.post(
        "/api/resumes",
        files={"file": ("payload.exe", b"MZ\x90\x00not-a-pdf", "application/octet-stream")},
    )
    assert exe.status_code == 400
    assert not (data_dir / "resumes" / "payload.exe").exists()
    oversized = client.post(
        "/api/resumes",
        files={"file": ("huge.pdf", b"%PDF" + b"x" * RESUME_MAX_BYTES, "application/pdf")},
    )
    assert oversized.status_code == 400


def test_gitignore_covers_uploaded_resumes():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in text
    assert "*.pdf" in text or "data/resumes" in text
    assert "Personal resumes" in text or "resumes" in text.lower()


def test_caps_and_schedule_settings_persist(tmp_path):
    client, store, _data_dir = _client(tmp_path)
    saved = client.post(
        "/api/settings",
        json={
            "max_applications_per_day": 7,
            "max_applications_per_run": 3,
            "schedule_windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}],
            "applicant_summary": "Azure data engineer in Dallas",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["quota"]["max_per_day"] == 7
    assert body["quota"]["max_per_run"] == 3
    assert body["schedule"]["windows"][0]["start"] == "09:00"
    assert store.get_setting("max_applications_per_day") == "7"
    assert store.get_setting("max_applications_per_run") == "3"
    assert store.get_setting("schedule_windows")
    assert "Dallas" in store.get_setting("applicant_summary")
    status = client.get("/api/status").json()
    assert status["quota"]["day_cap"] == 7
    assert status["quota"]["run_cap"] == 3
    assert status["schedule"]["enabled"] is True
    cleared = client.post("/api/settings", json={"schedule_windows": []})
    assert cleared.status_code == 200
    assert cleared.json()["schedule"]["enabled"] is False


def test_settings_and_models_mutations_reject_cross_origin(tmp_path):
    client, store, _data_dir = _client(tmp_path)
    headers = {"Origin": "http://evil.example"}
    assert client.post("/api/settings", json={"max_applications_per_day": 1}, headers=headers).status_code == 403
    assert client.post("/api/models/active", json={"model": "llama3.2"}, headers=headers).status_code == 403
    assert client.post("/api/models/chat", json={"message": "hi"}, headers=headers).status_code == 403
    assert store.get_setting("ollama_model") == ""


def test_questions_page_and_api_still_200(tmp_path):
    client, _store, _data_dir = _client(tmp_path)
    page = client.get("/questions")
    assert page.status_code == 200
    listed = client.get("/api/questions")
    assert listed.status_code == 200
    assert "questions" in listed.json()
    assert "counts" in listed.json()


def test_email_address_select_placeholder_classifies_as_email_text(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "email", "me@example.com")
    monkeypatch.setattr(config, "phone_number", "")
    client, store, _data_dir = _client(tmp_path)
    row = store.upsert_question(
        raw_question="Email address Email address Email address",
        field_kind="select",
        options=["Select an option", "[email]"],
    )
    assert row is not None
    store.save_question_classification(
        row["id"],
        cleaned_question="Email address Email address Email address",
        classified_kind="select",
        answer_shape="select",
        cluster_key="legacy-select",
        classification_status="heuristic",
    )
    for _ in range(8):
        store.upsert_question(
            raw_question="Email address Email address Email address",
            field_kind="select",
            options=["Select an option", "[email]"],
        )
    store.upsert_question(raw_question="Email", field_kind="text")
    payload = client.get("/api/questions", params={"status": "pending"}).json()
    assert len(payload["questions"]) == 1
    listed = payload["questions"][0]
    assert listed["question_text"] == "Email"
    assert listed["kind"] == "email"
    assert listed["classified_kind"] == "email"
    assert listed["answer_shape"] == "text"
    assert listed["options"] == []
    assert listed["prefill"] == "me@example.com"
    assert "[email]" not in (listed.get("options") or [])


def test_store_init_on_legacy_jobs_table_without_outcome(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-no-outcome.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            workplace TEXT,
            url TEXT,
            fit_score REAL,
            status TEXT NOT NULL,
            reason TEXT,
            screenshot_path TEXT,
            html_path TEXT,
            seen_at TEXT NOT NULL,
            applied_at TEXT,
            last_run_id TEXT
        );
        INSERT INTO jobs(job_id, title, status, seen_at)
        VALUES ('1', 'Data Engineer', 'seen', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    store = Store(str(path))
    cols = store._table_columns("jobs")
    assert "outcome" in cols
    row = store.get_job("1")
    assert row is not None
    store.upsert_job("1", title="Data Engineer", status="applied")
    assert store.get_job("1")["status"] == "applied"
    store.close()


def test_chat_empty_message_is_400(tmp_path):
    client, _store, _data_dir = _client(tmp_path)
    assert client.post("/api/models/chat", json={"message": "  "}).status_code == 400


def test_chat_snippet_saves_summary_and_seed(tmp_path):
    client, store, _data_dir = _client(tmp_path)
    summary = client.post(
        "/api/models/chat/snippet",
        json={"text": "Azure data engineer in Dallas", "target": "summary"},
    )
    assert summary.status_code == 200
    assert summary.json()["target"] == "summary"
    assert "Dallas" in store.get_setting("applicant_summary")
    seed = client.post(
        "/api/models/chat/snippet",
        json={"text": "How many years of SQL?", "target": "seed"},
    )
    assert seed.status_code == 200
    assert seed.json()["target"] == "seed"
    assert seed.json()["question"]["source"] in {"seed", "manual"}


def test_cannot_delete_active_resume_while_worker_runs(tmp_path):
    client, _store, worker, _ollama, data_dir = _harness(tmp_path)
    client.post("/api/resumes", files={"file": ("live.pdf", PDF_BYTES, "application/pdf")})
    worker.status = lambda: {
        "alive": True,
        "pid": 22,
        "phase": "running",
        "run": {"status": "running"},
        "last_error": "",
    }
    response = client.delete("/api/resumes/live.pdf")
    assert response.status_code == 409
    assert (data_dir / "resumes" / "live.pdf").is_file()
