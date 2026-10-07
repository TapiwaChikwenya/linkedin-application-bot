import json

from linkedin_easy_apply.inference import (
    conservative_inferred_value,
    integer_years,
    leftover_required_non_sensitive,
    skill_years_for_question,
    yes_no_family,
)
from linkedin_easy_apply.llm import answer_unanswered, build_inference_prompt
from linkedin_easy_apply.store import Store, match_approved_answers


def test_databricks_years_interpolate_from_spark_when_databricks_missing(monkeypatch):
    import config
    from linkedin import Linkedin

    monkeypatch.setattr(config, "years_experience", {"spark": 4, "sql": 9})
    inferred = skill_years_for_question(
        "How many years of Databricks experience do you have?",
        {"spark": 4, "sql": 9},
        kind="number",
    )
    assert inferred is not None
    assert inferred.value == "4"
    assert inferred.interpolated is True
    assert inferred.note == "Interpolated years 4 from spark"

    bot = Linkedin.__new__(Linkedin)
    field = {
        "question": "How many years of Databricks experience do you have?",
        "kind": "number",
    }
    assert bot.mapped_value_for_field(field) == "4"
    assert field["_inference_note"] == "Interpolated years 4 from spark"
    assert bot.mapped_value_for_field({
        "question": "How many years of COBOL experience do you have?",
        "kind": "number",
    }) == ""


def test_databricks_years_interpolate_from_approved_spark(tmp_path):
    store = Store(str(tmp_path / "interp-spark.sqlite"))
    spark = store.upsert_question(
        raw_question="How many years of Spark experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="4",
    )
    store.approve_answer(spark["id"], "4")
    matches = store.retrieve_approved_answers([
        {"question": "How many years of Databricks experience do you have?", "kind": "number"},
    ])
    assert matches[0]["value"] == "4"
    assert matches[0]["match"] == "interpolated"
    assert matches[0]["note"] == "Interpolated years 4 from spark"
    store.close()


def test_sponsorship_is_not_inferred_from_work_authorization(tmp_path):
    store = Store(str(tmp_path / "no-sponsor-from-auth.sqlite"))
    auth = store.upsert_question(
        raw_question="Are you legally authorized to work in the United States?",
        field_kind="select",
        options=["Yes", "No"],
        source="manual",
        proposed_value="Yes",
    )
    store.approve_answer(auth["id"], "Yes")
    assert yes_no_family("Are you legally authorized to work in the United States?") == "work_auth"
    assert yes_no_family("Will you require sponsorship?") == "sponsorship"
    skipped = store.retrieve_approved_answers([
        {"question": "Will you require sponsorship?", "kind": "select"},
    ])
    assert skipped == []

    related = store.retrieve_approved_answers([
        {"question": "Do you have US work authorization?", "kind": "select"},
    ])
    assert related[0]["value"] == "Yes"
    assert related[0]["match"] == "interpolated"
    store.close()


def test_integer_years_only_and_never_global_default():
    assert integer_years(4) == "4"
    assert integer_years("4 years") == "4"
    assert integer_years("about four") == ""
    fallback = conservative_inferred_value(
        {"question": "How many years of COBOL experience do you have?", "kind": "number"},
        {"sql": 9, "default": 9},
    )
    assert fallback is None
    named = conservative_inferred_value(
        {"question": "How many years of SQL experience do you have?", "kind": "number"},
        {"sql": 9, "default": 9},
    )
    assert named is not None
    assert named.value == "9"
    assert named.interpolated is False


def test_answer_unanswered_second_pass_when_first_empty(monkeypatch):
    from linkedin_easy_apply import llm

    prompts: list[str] = []

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
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(str(body.get("prompt") or ""))
        if len(prompts) == 1:
            return FakeResponse({"response": json.dumps({"answers": []})})
        return FakeResponse({
            "response": json.dumps({
                "answers": [{"question_id": "q0", "value": "4"}],
            })
        })

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    question = {
        "question": "How many years of Databricks experience do you have?",
        "kind": "number",
        "required": True,
    }
    answers = answer_unanswered(
        {"years_experience": {"spark": 4}},
        "Databricks years",
        [question],
        config_module=Cfg,
    )
    assert len(prompts) == 2
    assert "omit only if there is no evidence" in prompts[1].casefold()
    assert "infer only from applicant_facts" in prompts[1].casefold()
    assert answers[0]["value"] == "4"
    assert answers[0]["inferred"] is True


def test_answer_unanswered_skips_second_pass_for_sensitive(monkeypatch):
    from linkedin_easy_apply import llm

    prompts: list[str] = []

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
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(str(body.get("prompt") or ""))
        return FakeResponse({"response": json.dumps({"answers": []})})

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    answers = answer_unanswered(
        {"requires_sponsorship": "No"},
        "Sponsorship",
        [{
            "question": "Will you require sponsorship?",
            "kind": "select",
            "options": ["Yes", "No"],
            "required": True,
        }],
        config_module=Cfg,
    )
    assert answers == []
    assert len(prompts) == 1
    leftover = leftover_required_non_sensitive(
        [{
            "question": "Will you require sponsorship?",
            "kind": "select",
            "required": True,
        }],
        [],
    )
    assert leftover == []


def test_fill_known_fields_sensitive_still_skips_non_sensitive_does_not(monkeypatch, tmp_path):
    import config
    from linkedin import Linkedin

    store = Store(str(tmp_path / "infer-skip.sqlite"))
    monkeypatch.setattr(config, "application_city", "")
    monkeypatch.setattr(config, "years_experience", {"spark": 4})
    monkeypatch.setattr(config, "yes_no_answers", [])
    monkeypatch.setattr(config, "phone_number", "")

    logged: list[str] = []
    filled: list[tuple[str, str]] = []
    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.current_job_details = {"title": "Data Engineer", "company": "Acme"}
    bot.observe = lambda msg, *args, **kwargs: logged.append(str(msg))
    fields = [
        {
            "question": "How many years of Databricks experience do you have?",
            "kind": "number",
            "value": "",
            "required": True,
            "options": [],
        },
        {
            "question": "What is your favorite color?",
            "kind": "text",
            "value": "",
            "required": True,
            "options": [],
        },
        {
            "question": "Will you require sponsorship?",
            "kind": "select",
            "options": ["Yes", "No"],
            "value": "",
            "required": True,
        },
    ]
    bot.collect_form_state = lambda dialog: (fields, "form")
    bot.attachResume = lambda dialog: None
    bot.fill_control = lambda field, value: filled.append((field["question"], value)) or True
    bot.fill_llm_fields = lambda *args, **kwargs: None

    reason = bot.fillKnownFields(None, "4466303632")
    assert ("How many years of Databricks experience do you have?", "4") in filled
    assert "Interpolated years 4 from spark" in logged
    assert any(item.startswith("Inferred: How many years of Databricks") for item in logged)
    assert not any("resume" in item.casefold() and "built" in item.casefold() for item in logged)
    assert reason == "Unanswered required question: Will you require sponsorship?"
    assert "Skipped sensitive unanswered" in logged
    assert reason != "Unanswered required question: What is your favorite color?"
    store.close()


def test_build_inference_prompt_forbids_inventing_sensitive_facts():
    prompt = build_inference_prompt(
        {"resume_text": "Spark and SQL engineer"},
        "form",
        [{"question": "How many years of Databricks experience?", "kind": "number", "required": True}],
    )
    lowered = prompt.casefold()
    assert "infer only from applicant_facts" in lowered
    assert "omit only if there is no evidence" in lowered
    assert "never infer sponsorship from work authorization" in lowered
    assert "spark and sql engineer" in lowered


def test_match_approved_answers_keeps_exact_ahead_of_interpolation(tmp_path):
    store = Store(str(tmp_path / "exact-first.sqlite"))
    spark = store.upsert_question(
        raw_question="How many years of Spark experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="4",
    )
    store.approve_answer(spark["id"], "4")
    databricks = store.upsert_question(
        raw_question="How many years of Databricks experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="3",
    )
    store.approve_answer(databricks["id"], "3")
    pool = store.list_approved_questions()
    matched = match_approved_answers(
        pool,
        [{"question": "How many years of Databricks experience do you have?"}],
    )
    assert matched[0]["value"] == "3"
    assert matched[0]["match"] == "exact"
    store.close()
