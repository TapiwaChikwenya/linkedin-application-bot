from fastapi.testclient import TestClient

from linkedin_easy_apply.dashboard.app import create_app
from linkedin_easy_apply.store import Store
from linkedin_easy_apply.worker import (
    WorkerManager,
    find_owned_worker_pid,
    resolve_live_owned_pid,
    write_pid,
    write_state,
)


class StubOllama:
    def __init__(self, payload=None):
        self.ensure_calls = 0
        self.stop_calls = 0
        self.pull_calls = []
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
        return dict(self.payload)

    def stop(self):
        self.stop_calls += 1
        return {"ok": True, "message": "Ollama was not started by this dashboard."}

    def pull(self, model):
        self.pull_calls.append(model)
        self.payload = dict(self.payload)
        self.payload["ok"] = True
        self.payload["pulling"] = True
        self.payload["pull_model"] = model
        self.payload["detail"] = "pulling model " + model
        return dict(self.payload)


def _harness(tmp_path, ollama=None):
    store = Store(str(tmp_path / "assistant.sqlite"))
    worker = WorkerManager(store, str(tmp_path))
    sidecar = ollama if ollama is not None else StubOllama()
    client = TestClient(
        create_app(store=store, worker=worker, ollama=sidecar, data_dir=str(tmp_path))
    )
    return client, store, worker, sidecar


def _client(tmp_path):
    client, store, _worker, _ollama = _harness(tmp_path)
    return client, store


def test_dashboard_home_renders(tmp_path):
    client, _store = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "APPLY/OPS" in response.text
    assert "Start run" in response.text
    assert "href=\"/questions\"" in response.text
    assert "Start run = Ollama + worker" in response.text
    assert 'id="loginBtn"' in response.text
    assert "CAPS" in response.text
    assert "PACE" in response.text
    assert "SEARCH" in response.text
    assert 'id="tokenState"' in response.text
    assert 'id="llmState"' in response.text
    assert "need answers" in response.text
    assert "/questions#seeds" in response.text
    assert "Generate seed questions" in response.text
    assert 'href="/questions"' in response.text
    assert "/api/login" in response.text
    assert 'id="errorBar"' in response.text
    assert "prefers-reduced-motion" in response.text
    assert "tabular-nums" in response.text
    assert 'id="metricsStrip"' in response.text
    assert "<kbd>S</kbd>" in response.text
    assert 'id="fitState"' in response.text
    assert "skipped before open" in response.text
    assert "dec-skip" in response.text


def test_console_pages_render(tmp_path):
    client, _store = _client(tmp_path)
    questions = client.get("/questions")
    assert questions.status_code == 200
    assert "Approve" in questions.text
    assert "Skip" in questions.text
    assert "Generate seed questions" in questions.text
    assert "Memory queue" in questions.text
    assert 'id="seeds"' in questions.text
    assert "never auto-approved" in questions.text
    assert 'id="seedBtn"' in questions.text
    assert "/api/questions" in questions.text
    assert "/api/questions/generate-seeds" in questions.text
    assert "/api/questions/classify-pending" in questions.text
    assert "/api/questions/approve" in questions.text
    assert "/api/questions/reject" in questions.text
    assert client.get("/applications").status_code == 200
    applications = client.get("/applications")
    assert applications.status_code == 200
    assert 'id="metricsStrip"' in applications.text
    assert "data-status=\"skipped\"" in applications.text
    assert "Reason" in applications.text
    assert "Fit" in applications.text
    diagnostics = client.get("/diagnostics")
    assert diagnostics.status_code == 200
    assert "Latest failure" in diagnostics.text
    assert "linkedin" in questions.text
    assert "data-origin=\"seed\"" in questions.text
    assert "A approve" in questions.text
    assert "cleaned_question" in questions.text
    assert "answer_shape" in questions.text
    assert 'inputKinds = ["email", "tel", "text", "number"]' in questions.text
    assert "row.cleaned_question || row.question_text" in questions.text
    assert "isPlaceholderOption" in questions.text
    models = client.get("/models")
    assert models.status_code == 200
    assert "Ollama models" in models.text
    assert "Resume PDF" in models.text
    assert "Model chat" in models.text
    assert "Schedule" in models.text
    assert client.get("/settings").status_code == 200


def test_live_console_is_operator_shell(tmp_path):
    client, _store = _client(tmp_path)
    home = client.get("/")
    assert home.status_code == 200
    assert "APPLY/OPS" in home.text
    assert "Start run" in home.text
    assert "WORKER" in home.text
    assert "OLLAMA" in home.text
    assert "EVENT STREAM" in home.text
    assert "CURRENT JOB" in home.text
    assert "01 SEARCH" in home.text
    assert 'data-chart="sectors"' in home.text
    assert 'class="card"' not in home.text
    assert "is-running" in home.text
    assert "crosshair" in home.text


def test_start_rejected_when_worker_already_running(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda _path: False)
    client, _store, worker, ollama = _harness(tmp_path)
    worker.status = lambda: {
        "alive": True,
        "pid": 15964,
        "phase": "running",
        "run": {"status": "running"},
        "last_error": "",
    }
    response = client.post("/api/run/start")
    assert response.status_code == 409
    assert "already running" in response.json()["error"]
    assert ollama.ensure_calls == 0


def test_jobs_api_and_status(tmp_path, monkeypatch):
    client, store = _client(tmp_path)
    run_id = store.start_run("dash1")
    store.upsert_job(
        "999",
        title="Azure Data Engineer",
        company="Northwind",
        url="https://www.linkedin.com/jobs/view/999",
        status="applied",
        run_id=run_id,
    )
    jobs = client.get("/api/jobs", params={"q": "Northwind"}).json()
    assert jobs["jobs"][0]["title"] == "Azure Data Engineer"
    assert jobs["jobs"][0]["sector"] == "technology"
    assert jobs["jobs"][0]["market"] == "data"
    assert jobs["jobs"][0]["outcome"] == "applied"
    llm_payload = {
        "mode": "auto",
        "ready": True,
        "host": "http://127.0.0.1:11434",
        "model": "llama3.2",
        "detail": "model present",
    }
    monkeypatch.setattr(
        "linkedin_easy_apply.dashboard.app.llm_status",
        lambda: llm_payload,
    )
    status = client.get("/api/status").json()
    assert status["counts"]["applied"] == 1
    assert "quota" in status
    assert status["llm"] == llm_payload
    assert status["search"]["keyword"]
    assert status["last_job"]["title"] == "Azure Data Engineer"
    assert status["last_job"]["decision"] == "apply"
    assert "tokens" in status["ollama"]
    assert "metrics" in status
    assert "groups" in status
    assert status["hotkeys"]["start"] == "s"
    store.add_event(run_id, "info", "999", "Job fit llama=0.82 apply")
    store.upsert_job("999", status="applied", reason="applied", run_id=run_id, fit_score=0.82)
    status = client.get("/api/status").json()
    assert status["last_fit"]["score"] == 0.82
    assert status["last_fit"]["line"] == "Job fit llama=0.82 apply"
    store.add_event(
        run_id,
        "info",
        "888",
        "Skipped before open: intern. Job: https://www.linkedin.com/jobs/view/888",
    )
    status = client.get("/api/status").json()
    assert any("Skipped before open" in str(event.get("message") or "") for event in status["events"])
    assert status["last_fit"]["score"] == 0.82
    live = client.get("/")
    assert live.status_code == 200
    assert "skipped before open" in live.text
    assert 'id="fitState"' in live.text
    skipped = client.get("/api/jobs", params={"status": "skipped"}).json()
    assert skipped["groups"]["applied"] == 1
    metrics = client.get("/api/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body.get("source") in {"derived", "store"}
    assert "sectors" in body
    assert "markets" in body
    assert "locations" in body
    assert "applied_vs_skipped" in body
    assert "today" in body
    assert "last_7_days" in body
    assert "taxonomy" in body
    assert "healthcare" in body["taxonomy"]["sectors"]
    for window in (body["today"], body["last_7_days"]):
        assert "by_sector" in window
        assert "by_market" in window
        assert "by_location" in window
        assert "by_outcome" in window
        assert "totals" in window
    assert body["applied_vs_skipped"]["applied"] >= 1
    live = client.get("/")
    assert "/api/metrics" in live.text
    assert "if (!response.ok)" in live.text
    applications = client.get("/applications")
    assert applications.status_code == 200
    assert "/api/metrics" in applications.text


def test_start_rejected_when_profile_locked(tmp_path, monkeypatch):
    import config

    client, _store = _client(tmp_path)
    monkeypatch.setattr(config, "firefoxProfileRootDir", r"C:\fake-profile")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda path: True)
    monkeypatch.setattr("linkedin_easy_apply.worker.is_profile_locked", lambda path: True)
    response = client.post("/api/run/start")
    assert response.status_code == 409
    assert "Firefox" in response.json()["error"]
    assert "Close other Firefox" in response.json()["error"]
    status = client.get("/api/status").json()
    assert "Firefox" in status["last_error"]
    live = client.get("/")
    assert "Firefox profile is locked" in live.text or "errorBar" in live.text


def test_start_failure_reason_persists_on_status(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda _path: False)
    client, _store, worker, _ollama = _harness(tmp_path)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.resolve_worker_python",
        lambda _root: str(tmp_path / "missing-python.exe"),
    )
    response = client.post("/api/run/start")
    assert response.status_code == 409
    error = response.json()["error"]
    assert "Python" in error
    assert ".venv" in error
    status = client.get("/api/status").json()
    assert status["last_error"] == error
    assert worker.last_error == error


def test_artifact_path_is_restricted(tmp_path):
    client, _store = _client(tmp_path)
    assert client.get("/artifacts/../config.py").status_code in {400, 404}
    assert client.get("/artifacts/not-allowed.txt").status_code == 400


def test_job_load_artifact_is_allowed(tmp_path):
    (tmp_path / "job_load_123.html").write_text("<html>debug</html>", encoding="utf-8")
    client, _store = _client(tmp_path)
    listed = client.get("/api/artifacts").json()["artifacts"]
    assert any(item["name"] == "job_load_123.html" for item in listed)
    response = client.get("/artifacts/job_load_123.html")
    assert response.status_code == 200
    payload = client.get("/api/artifacts").json()
    assert "latest_failure" in payload


def test_latest_failure_payload(tmp_path):
    shot = tmp_path / "easy_apply_failure_999.png"
    shot.write_bytes(b"png")
    client, store = _client(tmp_path)
    store.upsert_job(
        "999",
        title="Azure Data Engineer",
        company="Northwind",
        status="failed",
        reason="modal stuck",
        screenshot_path=str(shot),
    )
    payload = client.get("/api/artifacts").json()
    assert payload["latest_failure"]["job_id"] == "999"
    assert payload["latest_failure"]["reason"] == "modal stuck"
    assert payload["latest_failure"]["screenshot"] == "easy_apply_failure_999.png"


def _owned_tree():
    return [
        (111, 1, "py.exe"),
        (222, 111, "python.exe"),
        (13496, 222, "geckodriver.exe"),
        (6624, 13496, "firefox.exe"),
        (9999, 4, "firefox.exe"),
    ]


def test_live_worker_heartbeat_outlives_tracked_launcher(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("live-worker")
    store.finish_run(run_id, "crashed")
    worker = WorkerManager(store, str(tmp_path))
    write_pid(111, worker.pid_path)
    write_state(
        {
            "pid": 222,
            "launcher_pid": 111,
            "token": "owned-run",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1036.0)
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda pid: pid == 222)
    monkeypatch.setattr("linkedin_easy_apply.worker.list_processes", _owned_tree)

    status = worker.status()

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["run"]["status"] == "running"


def test_launcher_exit_with_live_worker_tree_is_not_crashed(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("e2d5fe")
    worker = WorkerManager(store, str(tmp_path))
    write_pid(111, worker.pid_path)
    write_state(
        {
            "pid": 0,
            "launcher_pid": 111,
            "token": "starting",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1035.0)
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda pid: pid in {222, 13496, 6624})
    monkeypatch.setattr("linkedin_easy_apply.worker.list_processes", _owned_tree)

    status = worker.status()

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["run"]["status"] == "running"


def test_worker_pid_gone_marks_run_crashed(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("stale-worker")
    worker = WorkerManager(store, str(tmp_path))
    write_state(
        {
            "pid": 333,
            "launcher_pid": 111,
            "token": "old-run",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1100.0)
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.list_processes",
        lambda: [(9999, 4, "firefox.exe"), (6624, 1, "firefox.exe")],
    )

    status = worker.status()

    assert status["alive"] is False
    assert status["run"]["status"] == "crashed"
    assert not (tmp_path / "data" / "worker.json").exists()


def test_stop_targets_only_owned_worker_tree(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("owned-worker")
    worker = WorkerManager(store, str(tmp_path))
    write_state(
        {
            "pid": 444,
            "launcher_pid": 111,
            "token": "owned-token",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    commands = []
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1001.0)
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda pid: pid == 444)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.list_processes",
        lambda: [(444, 111, "python.exe"), (9999, 4, "firefox.exe")],
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.sys.platform", "win32")
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.subprocess.run",
        lambda command, **_kwargs: commands.append(command),
    )

    result = worker.stop()

    assert result == {"ok": True, "pid": 444}
    assert commands == [["taskkill", "/PID", "444", "/T", "/F"]]
    assert store.current_run()["status"] == "stopped"


def test_stop_after_launcher_exit_kills_owned_python(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("owned-tree")
    worker = WorkerManager(store, str(tmp_path))
    write_state(
        {
            "pid": 0,
            "launcher_pid": 111,
            "token": "owned-token",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    commands = []
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1035.0)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.pid_is_alive",
        lambda pid: pid in {222, 13496, 6624},
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.list_processes", _owned_tree)
    monkeypatch.setattr("linkedin_easy_apply.worker.sys.platform", "win32")
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.subprocess.run",
        lambda command, **_kwargs: commands.append(command),
    )

    result = worker.stop()

    assert result == {"ok": True, "pid": 222}
    assert commands == [["taskkill", "/PID", "222", "/T", "/F"]]
    assert store.current_run()["status"] == "stopped"


def test_find_owned_worker_pid_ignores_unrelated_firefox():
    processes = _owned_tree()
    assert find_owned_worker_pid(111, alive_check=lambda pid: pid != 111, processes=processes) == 222
    assert find_owned_worker_pid(4, alive_check=lambda _pid: True, processes=processes) is None
    state = {
        "pid": 0,
        "launcher_pid": 111,
        "heartbeat": 1000.0,
        "started_at": 1000.0,
    }
    assert (
        resolve_live_owned_pid(
            state,
            now=1046.0,
            alive_check=lambda pid: pid in {222, 13496, 6624},
            processes=processes,
        )
        == 222
    )


def test_worker_process_lease_publishes_interpreter_pid(tmp_path, monkeypatch):
    import os

    from linkedin_easy_apply.worker import read_state, worker_process_lease

    path = tmp_path / "data" / "worker.json"
    monkeypatch.setenv("LINKEDIN_WORKER_STATE_PATH", str(path))
    monkeypatch.setenv("LINKEDIN_WORKER_TOKEN", "lease-token")
    monkeypatch.setenv("LINKEDIN_RUN_ID", "lease-run")
    with worker_process_lease():
        state = read_state(str(path))
        assert state is not None
        assert state["pid"] == os.getpid()
        assert state["token"] == "lease-token"
        assert state["run_id"] == "lease-run"


def test_questions_api_lists_and_filters(tmp_path):
    client, store = _client(tmp_path)
    store.record_question(
        "Years of Python?",
        kind="number",
        proposed_value="6",
        job_id="555",
        job_title="Data Engineer",
        job_company="Adventure Works",
    )
    store.record_question(
        "Sponsorship required?",
        kind="select",
        options=["Yes", "No"],
        proposed_value="No",
    )
    empty_status = client.get("/api/questions", params={"status": "approved"}).json()
    assert empty_status["questions"] == []
    pending = client.get("/api/questions", params={"status": "pending", "kind": "number"}).json()
    assert len(pending["questions"]) == 1
    row = pending["questions"][0]
    assert row["question_text"] == "Years of Python?"
    assert row["seen_count"] == 1
    assert row["last_job_title"] == "Data Engineer"
    assert row["proposed_value"] == "6"
    assert pending["counts"]["pending"] == 2
    searched = client.get("/api/questions", params={"q": "Adventure"}).json()
    assert searched["questions"][0]["last_job_company"] == "Adventure Works"
    assert searched["questions"][0]["origin"] == "linkedin"
    store.upsert_question(
        raw_question="Years of SQL?",
        field_kind="number",
        source="seed",
        provenance="seed",
        proposed_value="9",
    )
    seeds = client.get("/api/questions", params={"source": "seed"}).json()
    assert seeds["questions"][0]["origin"] == "seed"
    assert seeds["origins"]["seed"] >= 1


def test_questions_api_repairs_repeated_email_select(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "email", "me@example.com")
    monkeypatch.setattr(config, "phone_number", "")
    client, store = _client(tmp_path)
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
    assert listed["cleaned_question"] == "Email"
    assert listed["kind"] == "email"
    assert listed["classified_kind"] == "email"
    assert listed["answer_shape"] == "text"
    assert listed["options"] == []
    assert listed["prefill"] == "me@example.com"
    assert "[email]" not in (listed.get("options") or [])
    assert listed["seen_count"] >= 9


def test_questions_api_uses_cleaned_email_and_hides_dupes(tmp_path):
    client, store = _client(tmp_path)
    store.upsert_question(raw_question="Email address", field_kind="text")
    store.upsert_question(raw_question="Email", field_kind="text")
    payload = client.get("/api/questions", params={"status": "pending"}).json()
    assert len(payload["questions"]) == 1
    row = payload["questions"][0]
    assert row["question_text"] == "Email"
    assert row["kind"] == "email"
    assert row["classified_kind"] == "email"
    assert row["answer_shape"] == "text"
    assert payload["counts"]["pending"] == 1


def test_classify_pending_api_uses_ollama_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "linkedin_easy_apply.llm.status",
        lambda cfg=None: {
            "ready": True,
            "host": "http://127.0.0.1:11434",
            "model": "llama3.2",
        },
    )
    monkeypatch.setattr(
        "linkedin_easy_apply.llm.generate_json",
        lambda prompt, response_format, **kwargs: {
            "cleaned_question": "City of residence",
            "kind": "text",
            "answer_shape": "text",
            "confidence": 0.9,
        },
    )
    client, store = _client(tmp_path)
    store.upsert_question(
        raw_question="What is your current city of residence?",
        field_kind="text",
    )
    response = client.post("/api/questions/classify-pending", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["llm_used"] >= 1
    listed = client.get("/api/questions").json()["questions"]
    assert listed[0]["question_text"] == "City of residence"


def test_questions_approve_and_reject_persist(tmp_path):
    client, store = _client(tmp_path)
    store.record_question(
        "Authorized to work in the US?",
        kind="select",
        options=["Yes", "No"],
        proposed_value="Yes",
    )
    store.record_question("Cover letter?", kind="textarea", proposed_value="skip")
    approved = client.post(
        "/api/questions/approve",
        json={"question_id": "Authorized to work in the US?", "approved_value": "yes"},
    )
    assert approved.status_code == 200
    payload = approved.json()["question"]
    assert payload["status"] == "approved"
    assert payload["approved_value"] == "Yes"
    stored = store.get_question("Authorized to work in the US?")
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["approved_value"] == "Yes"
    rejected = client.post(
        "/api/questions/reject",
        data={"question_id": "Cover letter?"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["question"]["status"] == "rejected"
    assert store.get_question("Cover letter?")["status"] == "rejected"


def test_questions_approve_validates(tmp_path):
    client, store = _client(tmp_path)
    store.record_question("City?", kind="text", proposed_value="Dallas")
    missing = client.post("/api/questions/approve", json={"question_id": "nope", "approved_value": "x"})
    assert missing.status_code == 404
    empty = client.post("/api/questions/approve", json={"question_id": "City?", "approved_value": ""})
    assert empty.status_code == 400


def test_question_routes_do_not_call_llm(tmp_path, monkeypatch):
    import linkedin_easy_apply.llm as llm_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("llm.generate must not be called")

    monkeypatch.setattr(llm_mod, "generate_json", boom)
    monkeypatch.setattr(llm_mod.OllamaClient, "generate", boom)
    import config

    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 9})
    monkeypatch.setattr(config, "resume_path", "")
    client, store = _client(tmp_path)
    store.record_question("City?", kind="text", proposed_value="Dallas")
    listed = client.get("/api/questions")
    assert listed.status_code == 200
    approved = client.post(
        "/api/questions/approve",
        json={"question_id": "City?", "approved_value": "Dallas"},
    )
    assert approved.status_code == 200
    rejected = client.post("/api/questions/reject", json={"question_id": "City?"})
    assert rejected.status_code == 200
    seeds = client.post("/api/questions/generate-seeds", json={})
    assert seeds.status_code == 200
    assert seeds.json()["ok"] is True


def test_questions_approve_while_writer_holds_db_briefly(tmp_path):
    import sqlite3
    import threading
    import time

    client, store = _client(tmp_path)
    row = store.record_question("Years of SQL?", kind="number", proposed_value="9")
    holding = threading.Event()
    release = threading.Event()
    result: dict[str, object] = {}

    def hold() -> None:
        conn = sqlite3.connect(store.path, timeout=5, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        holding.set()
        release.wait(timeout=2.0)
        conn.execute("COMMIT")
        conn.close()

    def post() -> None:
        result["resp"] = client.post(
            "/api/questions/approve",
            json={"question_id": str(row["id"]), "approved_value": "9"},
        )

    holder = threading.Thread(target=hold)
    holder.start()
    assert holding.wait(timeout=2)
    poster = threading.Thread(target=post)
    poster.start()
    time.sleep(0.15)
    release.set()
    poster.join(timeout=3)
    holder.join(timeout=3)
    resp = result.get("resp")
    assert resp is not None
    assert getattr(resp, "status_code", None) == 200
    assert resp.json()["question"]["status"] == "approved"


def test_questions_api_masks_contact_values(tmp_path):
    client, store = _client(tmp_path)
    store.record_question(
        "Notes for person@example.com?",
        kind="text",
        proposed_value="person@example.com",
        job_title="Reach me at 5551234567",
    )
    row = client.get("/api/questions").json()["questions"][0]
    blob = " ".join(str(value) for value in row.values())
    assert "person@example.com" not in blob
    assert "[email]" in row["proposed_value"] or "[email]" in row["question_text"]
    assert row["prefill"] == ""
    assert "5551234567" not in row["last_job_title"]
    assert "resume_text" not in row


def test_generate_seed_questions_api_inserts_pending_only(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 9})
    monkeypatch.setattr(config, "resume_path", "")
    client, store = _client(tmp_path)
    first = client.post("/api/questions/generate-seeds")
    assert first.status_code == 200
    payload = first.json()
    assert payload["inserted"] > 0
    assert payload["skipped"] == 0
    assert payload["counts"]["pending"] == payload["inserted"]
    second = client.post("/api/questions/generate-seeds")
    assert second.status_code == 200
    assert second.json()["inserted"] == 0
    assert second.json()["skipped"] == payload["inserted"]
    rows = store.list_pending_questions(limit=500)
    assert all(not str(row.get("approved_value") or "").strip() for row in rows)
    sql = next(row for row in rows if "sql" in row["normalized_question"] and row["field_kind"] == "number")
    assert sql["source"] in {"seed", "manual"}


def test_generate_seed_questions_cross_origin(tmp_path):
    client, store = _client(tmp_path)
    response = client.post(
        "/api/questions/generate-seeds",
        json={},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert store.question_counts()["total"] == 0


def test_questions_reject_cross_origin(tmp_path):
    client, store = _client(tmp_path)
    store.record_question("Onsite required?", kind="select", options=["Yes", "No"])
    response = client.post(
        "/api/questions/reject",
        json={"question_id": "Onsite required?"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert store.get_question("Onsite required?")["status"] == "pending"


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self._code = None

    def poll(self):
        return self._code


def test_start_waits_for_claimed_worker_pid_not_launcher(tmp_path, monkeypatch):
    import os
    import time

    import config
    from linkedin_easy_apply.worker import resolve_worker_python, write_state

    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda _path: False)
    client, _store, worker, ollama = _harness(tmp_path)
    captured = {}
    claimed = os.getpid()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        env = kwargs.get("env") or {}
        captured["env"] = env
        write_state(
            {
                "pid": claimed,
                "launcher_pid": 111,
                "token": env["LINKEDIN_WORKER_TOKEN"],
                "run_id": env["LINKEDIN_RUN_ID"],
                "heartbeat": time.time(),
                "started_at": time.time(),
            },
            worker.state_path,
        )
        return _FakeProc(111)

    monkeypatch.setattr("linkedin_easy_apply.worker.subprocess.Popen", fake_popen)
    response = client.post("/api/run/start")
    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["pid"] == claimed
    assert payload["launcher_pid"] == 111
    assert payload["pid"] != payload["launcher_pid"]
    assert ollama.ensure_calls == 1
    assert captured["cwd"] == str(tmp_path)
    assert captured["command"][0] == resolve_worker_python(str(tmp_path))
    assert captured["command"][-2:] == ["-m", "linkedin_easy_apply"]
    assert "src" in captured["env"].get("PYTHONPATH", "")


def test_start_uses_project_venv_python(tmp_path, monkeypatch):
    import os
    import sys
    import time

    import config
    from linkedin_easy_apply.worker import write_state

    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda _path: False)

    if sys.platform == "win32":
        python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    client, _store, worker, _ollama = _harness(tmp_path)
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        env = kwargs.get("env") or {}
        write_state(
            {
                "pid": os.getpid(),
                "launcher_pid": 111,
                "token": env["LINKEDIN_WORKER_TOKEN"],
                "run_id": env["LINKEDIN_RUN_ID"],
                "heartbeat": time.time(),
                "started_at": time.time(),
            },
            worker.state_path,
        )
        return _FakeProc(111)

    monkeypatch.setattr("linkedin_easy_apply.worker.subprocess.Popen", fake_popen)
    response = client.post("/api/run/start")
    assert response.status_code == 200
    assert captured["command"][0] == str(python)


def test_start_times_out_without_claimed_pid(tmp_path, monkeypatch):
    _client, _store, worker, _ollama = _harness(tmp_path)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.subprocess.Popen",
        lambda command, **kwargs: _FakeProc(111),
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda pid: False)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.subprocess.run",
        lambda *args, **kwargs: None,
    )
    result = worker.start(wait_seconds=0.0, poll_interval=0.0)
    assert result["ok"] is False
    assert "PID" in result["error"]
    assert worker.last_error


def test_stop_run_does_not_stop_ollama(tmp_path, monkeypatch):
    client, store, worker, ollama = _harness(tmp_path)
    run_id = store.start_run("keep-ollama")
    write_state(
        {
            "pid": 444,
            "launcher_pid": 111,
            "token": "owned-token",
            "run_id": run_id,
            "started_at": 1000.0,
            "heartbeat": 1000.0,
        },
        worker.state_path,
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.time.time", lambda: 1001.0)
    monkeypatch.setattr("linkedin_easy_apply.worker.pid_is_alive", lambda pid: pid == 444)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.subprocess.run",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("linkedin_easy_apply.worker.sys.platform", "win32")
    stopped = client.post("/api/run/stop")
    assert stopped.status_code == 200
    assert ollama.stop_calls == 0
    ollama_stop = client.post("/api/ollama/stop")
    assert ollama_stop.status_code == 200
    assert ollama.stop_calls == 1


def test_status_includes_ollama_questions_and_last_error(tmp_path, monkeypatch):
    client, _store = _client(tmp_path)
    monkeypatch.setattr(
        "linkedin_easy_apply.dashboard.app.llm_status",
        lambda: {
            "mode": "ollama",
            "ready": True,
            "host": "http://127.0.0.1:11434",
            "model": "llama3.2",
            "detail": "model present",
        },
    )
    status = client.get("/api/status").json()
    assert "ollama" in status
    assert "questions" in status
    assert "last_error" in status
    assert status["llm"]["ready"] is True
    assert status["llm_ready"] is True
    assert status["pace"] in {"human", "fast"}
    assert "remaining" in status["quota"]
    assert "run_remaining" in status["quota"]
    assert "day_remaining" in status["quota"]
    assert "Start run = Ollama + worker" in status["start_contract"]
    assert "search" in status
    assert "last_job" in status
    assert "metrics" in status
    assert "hotkeys" in status
    assert "tokens" in status["ollama"]
    assert "last_fit" in status


def test_last_fit_survives_metrics_failure(tmp_path, monkeypatch):
    client, store = _client(tmp_path)
    run_id = store.start_run("fit-live")
    store.add_event(run_id, "info", "1", "Job fit llama=0.82 apply")

    def boom(*_args, **_kwargs):
        raise RuntimeError("metrics down")

    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.derive_metrics", boom)
    status = client.get("/api/status").json()
    assert status["last_fit"]["score"] == 0.82
    assert status["last_fit"]["decision"] == "apply"
    assert status["metrics"]["source"] == "unavailable"
    metrics = client.get("/api/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["source"] == "unavailable"
    assert body["sectors"] == []
    assert body["applied_vs_skipped"] == {"applied": 0, "skipped": 0, "failed": 0}
    assert client.get("/").status_code == 200
    assert client.get("/applications").status_code == 200


def test_login_opens_firefox_profile(tmp_path, monkeypatch):
    import config

    client, _store, worker, _ollama = _harness(tmp_path)
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    monkeypatch.setattr(config, "firefoxProfileRootDir", str(profile))
    called = {}

    def fake_login(path):
        called["path"] = path
        return {
            "ok": True,
            "message": "Firefox opened at LinkedIn login. Sign in, confirm the feed loads, then close that Firefox window before Start run.",
        }

    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.start_profile_login", fake_login)
    response = client.post("/api/login")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "Sign in" in payload["message"]
    assert called["path"] == str(profile)
    assert worker.last_error == ""


def test_login_blocked_when_worker_already_running(tmp_path, monkeypatch):
    import config

    client, _store, worker, _ollama = _harness(tmp_path)
    monkeypatch.setattr(config, "firefoxProfileRootDir", str(tmp_path))
    worker.status = lambda: {
        "alive": True,
        "pid": 15964,
        "phase": "running",
        "run": {"status": "running"},
        "last_error": "",
    }
    called = {"n": 0}

    def fake_login(_path):
        called["n"] += 1
        return {"ok": True, "message": "should not run"}

    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.start_profile_login", fake_login)
    response = client.post("/api/login")
    assert response.status_code == 409
    assert "already running" in response.json()["error"]
    assert called["n"] == 0
    status = client.get("/api/status").json()
    assert "already running" in status["last_error"]


def test_login_blocked_when_profile_locked(tmp_path, monkeypatch):
    import config

    client, _store = _client(tmp_path)
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    monkeypatch.setattr(config, "firefoxProfileRootDir", str(profile))
    monkeypatch.setattr(
        "linkedin_easy_apply.dashboard.app.start_profile_login",
        lambda _path: {"ok": False, "error": "Firefox profile is locked. Close other Firefox windows that use this bot profile, then Login again."},
    )
    response = client.post("/api/login")
    assert response.status_code == 409
    assert "locked" in response.json()["error"].lower()
    status = client.get("/api/status").json()
    assert "locked" in status["last_error"].lower()


def test_login_requires_firefox_profile(tmp_path, monkeypatch):
    import config

    client, _store = _client(tmp_path)
    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    response = client.post("/api/login")
    assert response.status_code == 409
    assert "FIREFOX_PROFILE_PATH" in response.json()["error"]


def test_login_reject_cross_origin(tmp_path):
    client, _store = _client(tmp_path)
    response = client.post("/api/login", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_start_combines_venv_and_ollama_errors(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr("linkedin_easy_apply.dashboard.app.is_profile_locked", lambda _path: False)
    ollama = StubOllama(
        {
            "ok": False,
            "ready": False,
            "host": "http://127.0.0.1:11434",
            "model": "llama3.2",
            "detail": "Ollama is not installed or not on PATH. Install from https://ollama.com/download then Start again.",
            "started": False,
            "pulling": False,
            "pid": None,
        }
    )
    client, _store, worker, _sidecar = _harness(tmp_path, ollama=ollama)
    monkeypatch.setattr(
        "linkedin_easy_apply.worker.resolve_worker_python",
        lambda _root: str(tmp_path / "missing-python.exe"),
    )
    response = client.post("/api/run/start")
    assert response.status_code == 409
    error = response.json()["error"]
    assert ".venv" in error
    assert "Ollama" in error
    assert worker.last_error == error
    live = client.get("/")
    assert "errorBar" in live.text

