from linkedin_easy_apply.job_fit import (
    FIT_RESPONSE_FORMAT,
    FIT_TIMEOUT_MIN_SEC,
    FIT_TIMEOUT_SEC,
    JobFitResult,
    build_fit_prompt,
    compact_fit_profile,
    evaluate_listing_fit,
    job_fit_timeout_sec,
    last_fit_status,
    normalize_fit_payload,
    parse_fit_log,
    snippet_is_usable,
)
from linkedin_easy_apply.llm import GenerateJsonResult
from linkedin_easy_apply.page_snapshot import unanswered_required_reason

LONG_SNIPPET = (
    "Senior Data Engineer for Azure Data Factory, Databricks, and SQL Server pipelines "
    "supporting analytics products."
)


def test_snippet_is_usable_requires_substance():
    assert snippet_is_usable("short") is False
    assert snippet_is_usable(LONG_SNIPPET) is True


def test_normalize_fit_payload_skips_below_threshold_even_if_apply():
    result = normalize_fit_payload(
        {"fit": 0.4, "reason": "weak overlap", "decision": "apply"},
        source="card",
    )
    assert result.skip_job is True
    assert result.decision == "skip"
    assert result.fit == 0.4


def test_normalize_fit_payload_skips_on_decision_skip():
    result = normalize_fit_payload(
        {"fit": 0.9, "reason": "staffing recruiter", "decision": "skip"},
        source="card",
    )
    assert result.skip_job is True
    assert result.log_line() == "Job fit llama=0.90 skip: staffing recruiter"


def test_normalize_fit_payload_applies_when_fit_high():
    result = normalize_fit_payload(
        {"fit": 0.82, "reason": "matches data engineer skills", "decision": "apply"},
        source="description",
    )
    assert result.skip_job is False
    assert result.log_line() == "Job fit llama=0.82 apply"


def test_invalid_payload_does_not_skip_job():
    result = normalize_fit_payload({}, source="card")
    assert result.ready is False
    assert result.skip_job is False


def test_evaluate_listing_fit_prefers_card_and_logs_apply():
    captured = {}

    def fake_generate(prompt, response_format, **kwargs):
        captured["prompt"] = prompt
        captured["format"] = response_format
        captured["timeout"] = kwargs.get("timeout")
        return {"fit": 0.82, "reason": "keyword match", "decision": "apply"}

    result = evaluate_listing_fit(
        title="Senior Data Engineer",
        company="Acme",
        snippet=LONG_SNIPPET,
        source="card",
        generate_json=fake_generate,
    )
    assert result.skip_job is False
    assert result.source == "card"
    assert result.log_line() == "Job fit llama=0.82 apply"
    assert captured["format"] == FIT_RESPONSE_FORMAT
    assert captured["timeout"] == FIT_TIMEOUT_SEC
    assert FIT_TIMEOUT_SEC >= 180
    assert FIT_TIMEOUT_MIN_SEC >= 90
    assert "Do not invent" in captured["prompt"]
    assert "Senior Data Engineer" in captured["prompt"]


def test_evaluate_listing_fit_skips_staffing_junk():
    result = evaluate_listing_fit(
        title="Talent Sourcer",
        company="Staffing Mill",
        snippet=LONG_SNIPPET,
        source="card",
        generate_json=lambda *_args, **_kwargs: {
            "fit": 0.21,
            "reason": "staffing recruiter",
            "decision": "skip",
        },
    )
    assert result.skip_job is True
    assert result.log_line() == "Job fit llama=0.21 skip: staffing recruiter"


def test_short_card_snippet_defers_instead_of_scoring():
    called = {"n": 0}

    def fake_generate(*_args, **_kwargs):
        called["n"] += 1
        return {"fit": 0.9, "reason": "should not run", "decision": "apply"}

    result = evaluate_listing_fit(
        title="Data Engineer",
        company="Acme",
        snippet="too short",
        source="card",
        generate_json=fake_generate,
    )
    assert result.needs_description is True
    assert result.skip_job is False
    assert called["n"] == 0
    assert "deferred" in result.log_line()


def test_ollama_down_does_not_skip_or_block():
    result = evaluate_listing_fit(
        title="Data Engineer",
        company="Acme",
        snippet=LONG_SNIPPET,
        source="description",
        generate_json=lambda *_args, **_kwargs: {},
    )
    assert result.ready is False
    assert result.skip_job is False
    assert result.log_line() == "Job fit llama skipped: Ollama not ready"


def test_fit_skip_message_mapping():
    cases = (
        ("timeout", "generate timeout (180s)"),
        ("unreachable", "Ollama not reachable"),
        ("missing_model", "model missing"),
        ("disabled", "Ollama not ready"),
        ("not_ready", "Ollama not ready"),
    )
    for kind, detail in cases:
        result = evaluate_listing_fit(
            title="Data Engineer",
            company="Acme",
            snippet=LONG_SNIPPET,
            source="description",
            generate_json=lambda *_args, kind=kind, detail=detail, **_kwargs: GenerateJsonResult(
                payload={},
                error_kind=kind,
                error=detail,
                timeout_sec=FIT_TIMEOUT_SEC,
            ),
        )
        assert result.ready is False
        assert result.skip_job is False
        assert result.log_line() == "Job fit llama skipped: " + detail
        if kind == "timeout":
            assert "not ready" not in result.log_line()


def test_fit_timeout_retries_shorter_prompt_then_applies():
    prompts = []

    def fake_generate(prompt, response_format, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            return GenerateJsonResult(
                payload={},
                error_kind="timeout",
                error="generate timeout (180s)",
                timeout_sec=FIT_TIMEOUT_SEC,
            )
        return {"fit": 0.8, "reason": "keyword match", "decision": "apply"}

    result = evaluate_listing_fit(
        title="Data Engineer",
        company="Acme",
        snippet=LONG_SNIPPET,
        source="description",
        generate_json=fake_generate,
    )
    assert result.skip_job is False
    assert result.ready is True
    assert result.log_line() == "Job fit llama=0.80 apply"
    assert len(prompts) == 2
    assert "applicant_profile" in prompts[0]
    assert "title and snippet only" in prompts[1]
    assert "applicant_profile" not in prompts[1]
    assert "approved_facts" not in prompts[1]


def test_build_fit_prompt_is_grounded_in_keywords_only(monkeypatch, tmp_path):
    import config
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "fit.sqlite"))
    row = store.upsert_question(
        raw_question="How many years of SQL experience?",
        field_kind="number",
        source="manual",
    )
    store.approve_answer(row["id"], "9")
    monkeypatch.setattr(config, "keywords", ["Data Engineer", "SQL Developer"])
    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 12})
    profile = compact_fit_profile(config, store)
    assert "default" not in profile["skills"]
    assert "sql" in profile["skills"]
    prompt = build_fit_prompt("Data Engineer", "Acme", LONG_SNIPPET, profile)
    assert "Data Engineer" in prompt
    assert "Do not invent" in prompt
    assert "resume_text" not in prompt
    assert "How many years of SQL experience?" in prompt
    compact = build_fit_prompt("Data Engineer", "Acme", LONG_SNIPPET, profile, compact=True)
    assert "title and snippet only" in compact
    assert "applicant_profile" not in compact
    assert "approved_facts" not in compact
    store.close()


def test_parse_fit_log_and_last_fit_status():
    parsed = parse_fit_log("Job fit llama=0.21 skip: staffing recruiter")
    assert parsed["score"] == 0.21
    assert parsed["decision"] == "skip"
    events = [{"message": "Job fit llama=0.82 apply", "job_id": "1"}]
    status = last_fit_status(events, {"fit_score": 0.1, "status": "applied", "job_id": "1"})
    assert status["line"] == "Job fit llama=0.82 apply"
    from_job = last_fit_status([], {"fit_score": 0.21, "status": "skipped_fit", "job_id": "2"})
    assert from_job["decision"] == "skip"
    assert from_job["score"] == 0.21


def test_job_fit_timeout_sec_defaults_180_floors_at_90(monkeypatch):
    monkeypatch.delenv("LINKEDIN_JOB_FIT_TIMEOUT_SEC", raising=False)

    class Empty:
        pass

    assert job_fit_timeout_sec(Empty) == FIT_TIMEOUT_SEC
    assert FIT_TIMEOUT_SEC >= 180
    class Low:
        job_fit_timeout_sec = 25
    assert job_fit_timeout_sec(Low) == FIT_TIMEOUT_MIN_SEC
    class High:
        job_fit_timeout_sec = 240
    assert job_fit_timeout_sec(High) == 240


def test_timeout_while_ready_does_not_log_ollama_not_ready(monkeypatch):
    import json

    from linkedin_easy_apply import llm

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else request
        if str(url).endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "unit-model:latest"}]})
        raise TimeoutError("timed out")

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"
        job_fit_timeout_sec = FIT_TIMEOUT_SEC

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    result = evaluate_listing_fit(
        title="Data Engineer",
        company="Acme",
        snippet=LONG_SNIPPET,
        source="description",
        config_module=Cfg,
    )
    assert result.ready is False
    assert result.skip_job is False
    assert result.error_kind == "timeout"
    assert result.log_line() == "Job fit llama skipped: generate timeout (180s)"
    assert "not ready" not in result.log_line().lower()


def test_last_fit_status_timeout_error_class():
    timeout_line = "Job fit llama skipped: generate timeout (180s)"
    parsed = parse_fit_log(timeout_line)
    assert parsed["error_kind"] == "timeout"
    assert "not ready" not in parsed["line"].lower()
    events = [{"message": timeout_line, "job_id": "9"}]
    status = last_fit_status(events, None)
    assert status["error_kind"] == "timeout"
    assert status["reason"] == "generate timeout (180s)"
    unreachable = last_fit_status(
        [{"message": "Job fit llama skipped: Ollama not reachable", "job_id": "8"}],
        None,
    )
    assert unreachable["error_kind"] == "unreachable"


def test_unanswered_required_reason_skips_guessing():
    reason = unanswered_required_reason([
        {"question": "Phone", "kind": "tel", "value": "555", "required": True},
        {"question": "Favorite color?", "kind": "text", "value": "", "required": True},
        {"question": "Will you require sponsorship?", "kind": "select", "value": "", "required": True},
        {"question": "Resume", "kind": "file", "value": "", "required": True},
    ])
    assert reason == "Unanswered required question: Will you require sponsorship?"
    assert unanswered_required_reason([
        {"question": "Phone", "kind": "tel", "value": "555", "required": True},
        {"question": "Favorite color?", "kind": "text", "value": "", "required": True},
    ]) == ""


def test_apply_fit_gate_persists_skip_reason(tmp_path):
    from linkedin import Linkedin
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "fit-gate.sqlite"))
    run_id = store.start_run("fit1")
    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.run_id = run_id
    bot.observe = lambda *args, **kwargs: None
    bot.displayWriteResults = lambda *args, **kwargs: None
    bot.decide_job_fit = lambda details, job_id=None: JobFitResult(
        ready=True,
        fit=0.21,
        decision="skip",
        reason="staffing recruiter",
        source="card",
        skip_job=True,
    )
    skipped = bot.apply_fit_gate(
        {"title": "Recruiter", "company": "Agency", "line": "1 | Recruiter"},
        "4466303632",
        "https://www.linkedin.com/jobs/view/4466303632",
    )
    assert skipped is True
    job = store.list_jobs()[0]
    assert job["status"] == "skipped_fit"
    assert float(job["fit_score"]) == 0.21
    assert "staffing recruiter" in job["reason"]
    store.close()


def test_resolve_fit_snippet_prefers_usable_card_text():
    from linkedin import Linkedin

    bot = Linkedin.__new__(Linkedin)
    bot.extract_job_description = lambda: LONG_SNIPPET
    snippet, source = bot.resolve_fit_snippet({"snippet": LONG_SNIPPET, "description": ""})
    assert source == "card"
    assert snippet == LONG_SNIPPET
    snippet, source = bot.resolve_fit_snippet({"snippet": "tiny", "description": ""})
    assert source == "description"
    assert snippet == LONG_SNIPPET
