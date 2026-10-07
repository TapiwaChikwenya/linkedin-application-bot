from linkedin_easy_apply.facts import applicant_facts, extract_resume_text
from linkedin_easy_apply.llm import (
    OllamaClient,
    answer_unanswered,
    build_prompt,
    llm_mode,
    normalize_answers,
    parse_json_object,
    status,
    usable_answer,
)
from linkedin_easy_apply.page_snapshot import match_option, unanswered_fields


def test_parse_json_object_extracts_embedded_object():
    raw = "Sure.\n{\"answers\":[{\"question\":\"SQL years\",\"value\":\"9\"}]}\n"
    payload = parse_json_object(raw)
    assert normalize_answers(payload) == [{"question": "SQL years", "value": "9"}]


def test_usable_answer_rejects_empty_and_essays():
    assert usable_answer("Yes") is True
    assert usable_answer("") is False
    assert usable_answer("x" * 401) is False


def test_unanswered_fields_skips_filled_and_files():
    fields = [
        {"kind": "file", "question": "Resume", "value": ""},
        {"kind": "text", "question": "Phone", "value": "555"},
        {"kind": "select", "question": "Authorized?", "value": "Select an option", "options": ["Select an option", "Yes", "No"]},
        {"kind": "radio", "question": "Sponsorship?", "value": "", "options": ["Yes", "No"]},
    ]
    pending = unanswered_fields(fields)
    questions = [item["question"] for item in pending]
    assert questions == ["Authorized?", "Sponsorship?"]


def test_match_option_yes_no():
    assert match_option("Yes", ["Select", "Yes", "No"]) == "Yes"
    assert match_option("no", ["Yes", "No, I do not"]) == "No, I do not"


def test_llm_mode_and_status_when_disabled(monkeypatch):
    monkeypatch.setenv("LINKEDIN_LLM", "off")

    class Cfg:
        llm_mode = "off"
        ollama_host = "http://127.0.0.1:11434"
        ollama_model = "llama3.2"

    assert llm_mode(Cfg) == "off"
    info = status(Cfg)
    assert info["ready"] is False
    assert info["detail"] == "disabled"


def test_ollama_client_generate(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"response":"{\\"answers\\":[{\\"question_id\\":\\"q0\\",\\"value\\":\\"5551234\\"}]}"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        return FakeResponse()

    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    client = OllamaClient(host="http://127.0.0.1:11434", model="llama3.2")
    answers = client.answer_questions(
        {"phone": "5551234"},
        "Phone number",
        [{"question": "Phone", "kind": "tel", "options": []}],
    )
    assert captured["url"].endswith("/api/generate")
    assert b'"question_id"' in captured["body"]
    assert answers == [{"question": "Phone", "value": "5551234"}]


def test_build_prompt_contains_facts_and_questions():
    prompt = build_prompt(
        {"first_name": "Tapiwa", "resume_text": "Azure Data Factory"},
        "How many years of Azure Data Factory experience?",
        [{"question": "ADF years", "kind": "number", "options": []}],
    )
    assert "Tapiwa" in prompt
    assert "ADF years" in prompt
    assert '"question_id": "q0"' in prompt
    assert "Do not invent" in prompt
    assert "interpolate related skill years" in prompt


def test_normalize_answers_rejects_unknown_ids_and_invalid_field_values():
    questions = [
        {"question": "Authorized?", "kind": "select", "options": ["Yes", "No"]},
        {"question": "Years of SQL?", "kind": "number", "options": []},
    ]
    payload = {
        "answers": [
            {"question_id": "invented", "value": "Yes"},
            {"question_id": "q0", "value": "yes"},
            {"question_id": "q1", "value": "about nine years"},
        ]
    }
    assert normalize_answers(payload, questions) == [
        {"question": "Authorized?", "value": "Yes"},
    ]


def test_answer_unanswered_calls_tags_and_generate(monkeypatch):
    import json

    from linkedin_easy_apply import llm

    called = []

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
        called.append(url)
        if url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "unit-model:latest"}]})
        assert url.endswith("/api/generate")
        return FakeResponse({
            "response": json.dumps({
                "answers": [{"question_id": "q0", "value": "Built Airflow DAGs for 6 years."}]
            })
        })

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    answers = answer_unanswered(
        {"summary": "Built Airflow DAGs for 6 years."},
        "Briefly describe your Airflow experience.",
        [{"question": "Briefly describe your Airflow experience.", "kind": "textarea"}],
        config_module=Cfg,
    )
    assert called == [
        "http://unit-ollama:11434/api/tags",
        "http://unit-ollama:11434/api/generate",
    ]
    assert answers == [{
        "question": "Briefly describe your Airflow experience.",
        "value": "Built Airflow DAGs for 6 years.",
    }]


def test_status_is_not_ready_when_configured_model_is_missing(monkeypatch):
    import json

    from linkedin_easy_apply import llm

    class FakeResponse:
        def read(self):
            return json.dumps({"models": [{"name": "another-model:latest"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://missing-model:11434"
        ollama_model = "required-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr(
        "linkedin_easy_apply.llm.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )
    info = status(Cfg)
    assert info["ready"] is False
    assert info["detail"] == "Ollama is up; pull required-model"


def test_failed_status_is_not_cached_like_a_ready_result(monkeypatch):
    from linkedin_easy_apply import llm

    calls = {"n": 0}

    class Cfg:
        llm_mode = "auto"
        ollama_host = "http://127.0.0.1:11434"
        ollama_model = "llama3.2"

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise TimeoutError("ollama starting")

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", boom)
    monkeypatch.setattr("linkedin_easy_apply.llm.time.time", lambda: 1000.0)
    first = status(Cfg)
    assert first["ready"] is False
    monkeypatch.setattr("linkedin_easy_apply.llm.time.time", lambda: 1003.0)
    second = status(Cfg)
    assert calls["n"] == 2
    assert second["ready"] is False


def test_applicant_facts_include_config_values(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "FirstName", "Tapiwa")
    monkeypatch.setattr(config, "phone_number", "5550001111")
    monkeypatch.setattr(config, "resume_path", str(tmp_path / "missing.pdf"))
    facts = applicant_facts(config)
    assert facts["first_name"] == "Tapiwa"
    assert facts["phone"] == "5550001111"
    assert facts["resume_text"] == ""
    assert facts["approved_answers"] == []
    assert extract_resume_text("") == ""


def test_applicant_facts_omit_catchall_bootstrap_rules(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 12})
    monkeypatch.setattr(
        config,
        "yes_no_answers",
        [(("sponsorship",), "No"), (("experience",), "Yes")],
    )
    monkeypatch.setattr(config, "resume_path", str(tmp_path / "missing.pdf"))
    facts = applicant_facts(config)
    assert "default" not in facts["years_experience"]
    assert facts["years_experience"]["sql"] == 9
    assert facts["yes_no_rules"] == [{"when_all_keywords": ["sponsorship"], "answer": "No"}]


def test_mapped_value_uses_years_and_yes_no(monkeypatch):
    import config
    from linkedin import Linkedin

    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 5})
    monkeypatch.setattr(config, "yes_no_answers", [(("sponsorship",), "No")])
    bot = Linkedin.__new__(Linkedin)
    assert bot.mapped_value_for_field({
        "question": "How many years of SQL experience do you have?",
        "kind": "number",
    }) == "9"
    assert bot.mapped_value_for_field({
        "question": "Will you require sponsorship?",
        "kind": "select",
        "options": ["Yes", "No"],
    }) == "No"
    assert bot.mapped_value_for_field({
        "question": "How many years of COBOL experience do you have?",
        "kind": "number",
    }) == ""


def test_fill_llm_fields_applies_only_still_unanswered_dynamic_fields(monkeypatch):
    from linkedin import Linkedin
    from linkedin_easy_apply import facts, llm

    captured = {}
    filled = []

    def fake_answer(fact_values, snapshot, questions, images=None, **_kwargs):
        captured["facts"] = fact_values
        captured["snapshot"] = snapshot
        captured["questions"] = questions
        return [{"question": questions[0]["question"], "value": "Built Airflow DAGs for 6 years."}]

    monkeypatch.setattr(llm, "status", lambda: {"ready": True, "model": "llama3.2"})
    monkeypatch.setattr(llm, "answer_unanswered", fake_answer)
    monkeypatch.setattr(
        facts,
        "applicant_facts",
        lambda *args, **kwargs: {"summary": "Airflow engineer"},
    )

    bot = Linkedin.__new__(Linkedin)
    bot.fill_control = lambda field, value: filled.append((field["question"], value)) or True
    fields = [
        {
            "question": "Phone",
            "kind": "tel",
            "value": "5551234",
            "_answered": True,
            "_element": object(),
        },
        {
            "question": "Briefly describe your Airflow experience.",
            "kind": "textarea",
            "value": "",
            "_element": object(),
        },
    ]

    bot.fill_llm_fields(fields, "Application form")

    assert [question["question"] for question in captured["questions"]] == [
        "Briefly describe your Airflow experience."
    ]
    assert filled == [(
        "Briefly describe your Airflow experience.",
        "Built Airflow DAGs for 6 years.",
    )]
    assert fields[1]["_answered"] is True


def test_build_prompt_contains_approved_answers_and_source_ids():
    prompt = build_prompt(
        {"first_name": "Tapiwa"},
        "How many years of SQL experience?",
        [{"question": "How many years of SQL experience?", "kind": "number"}],
        approved_answers=[{
            "source_id": "7",
            "question": "How many years of SQL experience?",
            "value": "9",
        }],
    )
    assert '"approved_answers"' in prompt
    assert '"source_id": "7"' in prompt
    assert "Use approved_answers when present" in prompt
    assert "confidence" in prompt
    assert "source_ids" in prompt
    assert '"question_id": "q0"' in prompt


def test_answer_unanswered_skips_when_ollama_down(monkeypatch):
    from linkedin_easy_apply import llm

    class Cfg:
        llm_mode = "auto"
        ollama_host = "http://127.0.0.1:9"
        ollama_model = "llama3.2"

    called = {"generate": 0}

    def boom(*_args, **_kwargs):
        raise TimeoutError("down")

    def fake_generate(self, prompt, images=None):
        called["generate"] += 1
        return "{}"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", boom)
    monkeypatch.setattr(llm.OllamaClient, "generate", fake_generate)
    answers = answer_unanswered(
        {"summary": "x"},
        "form",
        [{"question": "SQL years", "kind": "number"}],
        config_module=Cfg,
    )
    assert answers == []
    assert called["generate"] == 0


def test_answer_unanswered_injects_retrieved_approved_answers(monkeypatch, tmp_path):
    import json

    from linkedin_easy_apply import llm
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "memory.sqlite"))
    row = store.upsert_question(
        raw_question="How many years of SQL experience?",
        field_kind="number",
        source="manual",
        proposed_value="9",
    )
    store.approve_answer(row["id"], "9")
    captured = {}

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
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({
            "response": json.dumps({
                "answers": [{
                    "question_id": "q0",
                    "value": "9",
                    "confidence": 0.9,
                    "source_ids": [str(row["id"])],
                }]
            })
        })

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    answers = answer_unanswered(
        {"first_name": "Tapiwa"},
        "SQL years",
        [{"question": "How many years of SQL experience?", "kind": "number"}],
        config_module=Cfg,
        store=store,
    )
    prompt = captured["body"]["prompt"]
    assert "approved_answers" in prompt
    assert "How many years of SQL experience?" in prompt
    assert answers[0]["value"] == "9"
    assert answers[0]["source_ids"] == [str(row["id"])]
    store.close()


def test_applicant_facts_include_compact_approved_slice(tmp_path):
    import config
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "facts.sqlite"))
    row = store.upsert_question(
        raw_question="How many years of SQL experience?",
        field_kind="number",
        source="manual",
        proposed_value="9",
    )
    store.approve_answer(row["id"], "9")
    extra = store.upsert_question(
        raw_question="What is your favorite color?",
        field_kind="text",
        source="manual",
        proposed_value="blue",
    )
    store.approve_answer(extra["id"], "blue")
    facts = applicant_facts(
        config,
        store=store,
        questions=[{"question": "How many years of SQL experience?"}],
    )
    assert facts["approved_answers"] == [{
        "source_id": str(row["id"]),
        "question": "How many years of SQL experience?",
        "value": "9",
    }]
    store.close()


def test_generate_json_uses_schema_and_temperature_zero(monkeypatch):
    import json

    from linkedin_easy_apply import llm
    from linkedin_easy_apply.job_fit import FIT_RESPONSE_FORMAT

    captured = {}

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
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"response": json.dumps({"fit": 0.82, "reason": "match", "decision": "apply"})})

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    payload = llm.generate_json(
        "score this job",
        FIT_RESPONSE_FORMAT,
        config_module=Cfg,
        timeout=25,
        num_predict=160,
    )
    assert payload["fit"] == 0.82
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["format"]["required"] == ["fit", "reason", "decision"]
    assert captured["timeout"] == 25


def test_generate_json_empty_when_ollama_down(monkeypatch):
    from linkedin_easy_apply import llm
    from linkedin_easy_apply.job_fit import FIT_RESPONSE_FORMAT

    class Cfg:
        llm_mode = "auto"
        ollama_host = "http://127.0.0.1:9"
        ollama_model = "llama3.2"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr(
        "linkedin_easy_apply.llm.urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("down")),
    )
    assert llm.generate_json("prompt", FIT_RESPONSE_FORMAT, config_module=Cfg) == {}
    result = llm.generate_json_result("prompt", FIT_RESPONSE_FORMAT, config_module=Cfg)
    assert result.payload == {}
    assert result.error_kind == llm.GENERATE_UNREACHABLE
    assert result.error == "Ollama not reachable"


def test_not_ready_reason_distinguishes_status_failures():
    from linkedin_easy_apply.llm import not_ready_reason

    assert not_ready_reason({"ready": True}) == ("", "")
    assert not_ready_reason({"ready": False, "mode": "off", "detail": "disabled"}) == (
        "disabled",
        "Ollama not ready",
    )
    assert not_ready_reason(
        {"ready": False, "detail": "Ollama not reachable: URLError"}
    ) == ("unreachable", "Ollama not reachable")
    assert not_ready_reason(
        {"ready": False, "detail": "Ollama is up; pull llama3.2", "model": "llama3.2"}
    ) == ("missing_model", "model missing")


def test_classify_generate_exception_timeout_is_not_not_ready():
    import urllib.error

    from linkedin_easy_apply.llm import classify_generate_exception

    kind, message = classify_generate_exception(TimeoutError("timed out"), timeout_sec=90)
    assert kind == "timeout"
    assert message == "generate timeout (90s)"
    kind, message = classify_generate_exception(TimeoutError("timed out"), timeout_sec=180)
    assert kind == "timeout"
    assert message == "generate timeout (180s)"
    wrapped = urllib.error.URLError(TimeoutError("timed out"))
    kind, message = classify_generate_exception(wrapped, timeout_sec=90)
    assert kind == "timeout"
    assert message == "generate timeout (90s)"
    kind, message = classify_generate_exception(
        urllib.error.URLError("connection refused"), timeout_sec=90
    )
    assert kind == "unreachable"
    assert message == "Ollama not reachable"
    missing = urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/generate",
        404,
        "Not Found",
        hdrs=None,
        fp=None,
    )
    kind, message = classify_generate_exception(missing, timeout_sec=90)
    assert kind == "missing_model"
    assert message == "model missing"


def test_generate_json_result_timeout_when_ollama_ready(monkeypatch):
    import json

    from linkedin_easy_apply import llm
    from linkedin_easy_apply.job_fit import FIT_RESPONSE_FORMAT, FIT_TIMEOUT_SEC

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

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    result = llm.generate_json_result(
        "score this job",
        FIT_RESPONSE_FORMAT,
        config_module=Cfg,
        timeout=FIT_TIMEOUT_SEC,
    )
    assert result.payload == {}
    assert result.error_kind == llm.GENERATE_TIMEOUT
    assert result.error == f"generate timeout ({FIT_TIMEOUT_SEC}s)"
    assert result.error == "generate timeout (180s)"
    assert "not ready" not in result.error


def test_generate_json_result_missing_model_from_status(monkeypatch):
    import json

    from linkedin_easy_apply import llm
    from linkedin_easy_apply.job_fit import FIT_RESPONSE_FORMAT

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
        return FakeResponse({"models": [{"name": "other:latest"}]})

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    result = llm.generate_json_result("prompt", FIT_RESPONSE_FORMAT, config_module=Cfg)
    assert result.payload == {}
    assert result.error_kind == llm.GENERATE_MISSING_MODEL
    assert result.error == "model missing"


def test_sensitive_llm_answers_stay_pending_even_after_success(tmp_path):
    from linkedin import Linkedin
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "sensitive.sqlite"))
    stored = store.upsert_proposed_answer(
        "Will you require sponsorship?",
        "No",
        source="llm",
        field_kind="select",
        options=["Yes", "No"],
    )
    assert stored["approval_status"] == "pending"
    store.record_successful_fills([{
        "question": "Will you require sponsorship?",
        "value": "No",
        "source": "llm",
        "question_id": stored["id"],
        "already_approved": False,
    }])
    again = store.get_question(stored["id"])
    assert again["approval_status"] == "pending"
    assert again["reuse_count"] == 0

    mapped = store.upsert_question(
        raw_question="How many years of SQL experience?",
        field_kind="number",
        source="mapped",
        proposed_value="9",
    )
    store.approve_answer(mapped["id"], "9")
    store.record_successful_fills([{
        "question": "How many years of SQL experience?",
        "value": "9",
        "source": "mapped",
        "mapped": True,
        "question_id": mapped["id"],
    }])
    reused = store.get_question(mapped["id"])
    assert reused["approval_status"] == "approved"
    assert reused["reuse_count"] == 1

    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot._session_answers = [{
        "question": "Will you require sponsorship?",
        "source": "llm",
        "question_id": stored["id"],
    }]
    bot.commit_answer_memory()
    still = store.get_question(stored["id"])
    assert still["approval_status"] == "pending"
    store.close()


def test_warmup_loads_model_within_budget(monkeypatch):
    import json

    from linkedin_easy_apply import llm

    captured = {}

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
        captured.setdefault("timeouts", []).append(timeout)
        if str(url).endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "unit-model:latest"}]})
        captured["generate_timeout"] = timeout
        return FakeResponse({"response": json.dumps({"ok": True})})

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    result = llm.warmup_ollama(config_module=Cfg, budget_sec=15)
    assert result["ok"] is True
    assert result["warmed"] is True
    assert result["detail"] == "Ollama warmup: model loaded"
    assert captured["generate_timeout"] <= 15
    assert "not ready" not in result["detail"].lower()


def test_warmup_timeout_is_not_not_ready(monkeypatch):
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

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    result = llm.warmup_ollama(config_module=Cfg, budget_sec=15)
    assert result["ready"] is True
    assert result["warmed"] is False
    assert result["error_kind"] == llm.GENERATE_TIMEOUT
    assert "generate timeout" in result["detail"]
    assert "not ready" not in result["detail"].lower()


def test_warmup_skips_when_ollama_off():
    from linkedin_easy_apply import llm

    class Cfg:
        llm_mode = "off"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    result = llm.warmup_ollama(config_module=Cfg, budget_sec=15)
    assert result["ok"] is False
    assert result["warmed"] is False
    assert result["error_kind"] == llm.GENERATE_DISABLED
    assert "Ollama not ready" in result["detail"]
