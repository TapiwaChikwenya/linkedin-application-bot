from linkedin_easy_apply.question_classifier import (
    classify_pending_questions,
    classify_question_with_llm,
    clean_display_text,
    heuristic_classify,
    repair_contact_classifications,
)
from linkedin_easy_apply.store import Store


def test_heuristic_email_is_text_answer_without_ollama(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Ollama must not be called for heuristic classify")

    monkeypatch.setattr("linkedin_easy_apply.llm.generate_json", boom)
    monkeypatch.setattr("linkedin_easy_apply.llm.status", lambda cfg=None: {"ready": False})
    store = Store(str(tmp_path / "assistant.sqlite"))
    row = store.upsert_question(raw_question="Email address", field_kind="text")
    assert row is not None
    assert row["classified_kind"] == "email"
    assert row["answer_shape"] == "text"
    assert row["cleaned_question"] == "Email"
    assert row["raw_question"] == "Email address"
    listed = store.list_questions(status="pending")
    assert len(listed) == 1
    assert listed[0]["kind"] == "email"
    store.close()


def test_repeated_email_select_placeholder_is_email_text():
    result = heuristic_classify(
        "Email address Email address Email address",
        "select",
        ["Select an option", "[email]"],
    )
    assert result["kind"] == "email"
    assert result["answer_shape"] == "text"
    assert result["cleaned_question"] == "Email"
    assert clean_display_text("Email address Email address Email address") == "Email address"


def test_repeated_email_select_upsert_and_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr("linkedin_easy_apply.llm.status", lambda cfg=None: {"ready": False})
    store = Store(str(tmp_path / "email-select.sqlite"))
    row = store.upsert_question(
        raw_question="Email address Email address Email address",
        field_kind="select",
        options=["Select an option", "[email]"],
    )
    assert row is not None
    assert row["classified_kind"] == "email"
    assert row["answer_shape"] == "text"
    assert row["cleaned_question"] == "Email"
    store.save_question_classification(
        row["id"],
        cleaned_question="Email address Email address Email address",
        classified_kind="select",
        answer_shape="select",
        cluster_key="legacy-select",
        classification_status="heuristic",
    )
    other = store.upsert_question(raw_question="Email", field_kind="text")
    assert other is not None
    broken = store.get_question(row["id"])
    assert broken is not None
    assert broken["classified_kind"] == "select"
    repaired = repair_contact_classifications(store)
    assert repaired >= 1
    listed = store.list_questions(status="pending")
    assert len(listed) == 1
    assert listed[0]["classified_kind"] == "email"
    assert listed[0]["answer_shape"] == "text"
    assert listed[0]["cleaned_question"] == "Email"
    assert listed[0]["seen_count"] >= 2
    store.close()


def test_classify_pending_repairs_email_select(tmp_path, monkeypatch):
    monkeypatch.setattr("linkedin_easy_apply.llm.status", lambda cfg=None: {"ready": False})

    def boom(*_args, **_kwargs):
        raise AssertionError("generate_json must not run when Ollama is down")

    monkeypatch.setattr("linkedin_easy_apply.llm.generate_json", boom)
    store = Store(str(tmp_path / "pending-email.sqlite"))
    row = store.upsert_question(
        raw_question="Email address Email address Email address",
        field_kind="select",
        options=["Select an option", "[email]"],
    )
    store.save_question_classification(
        row["id"],
        cleaned_question="Email address Email address Email address",
        classified_kind="select",
        answer_shape="select",
        cluster_key="legacy-select",
        classification_status="heuristic",
    )
    payload = classify_pending_questions(store)
    assert payload["ok"] is True
    listed = store.list_questions(status="pending")
    assert listed[0]["classified_kind"] == "email"
    assert listed[0]["answer_shape"] == "text"
    assert listed[0]["cleaned_question"] == "Email"
    store.close()


def test_city_placeholder_select_becomes_text():
    city = heuristic_classify("City of residence", "select", ["Select an option"])
    assert city["kind"] == "text"
    assert city["answer_shape"] == "text"
    phone = heuristic_classify("Mobile phone number", "select", ["Select an option", "[phone]"])
    assert phone["kind"] == "tel"


def test_email_address_merges_with_email(tmp_path):
    store = Store(str(tmp_path / "merge.sqlite"))
    first = store.upsert_question(raw_question="Email address", field_kind="text")
    second = store.upsert_question(raw_question="Email", field_kind="text")
    assert first is not None and second is not None
    listed = store.list_questions(status="pending")
    assert len(listed) == 1
    assert listed[0]["cleaned_question"] == "Email"
    assert listed[0]["classified_kind"] == "email"
    assert listed[0]["seen_count"] == 2
    hidden = [
        row
        for row in store.list_questions(include_hidden=True, limit=50)
        if row.get("merged_into_id")
    ]
    assert len(hidden) == 1
    assert hidden[0]["merged_into_id"] == listed[0]["id"]
    assert hidden[0]["classification_status"] == "duplicate"
    store.close()


def test_ollama_mock_returns_cleaned_wording(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "llm.sqlite"))
    row = store.upsert_question(
        raw_question="What is your current city of residence?",
        field_kind="text",
    )
    assert row is not None
    assert row["classification_status"] == "heuristic"

    monkeypatch.setattr(
        "linkedin_easy_apply.llm.status",
        lambda cfg=None: {
            "ready": True,
            "host": "http://127.0.0.1:11434",
            "model": "llama3.2",
        },
    )

    def fake_generate_json(prompt, response_format, **_kwargs):
        assert "Do not invent" in prompt or "do not invent" in prompt.lower()
        assert "city of residence" in prompt.lower()
        assert "answers" not in str(response_format.get("required") or [])
        return {
            "cleaned_question": "City of residence",
            "kind": "text",
            "answer_shape": "text",
            "confidence": 0.88,
        }

    monkeypatch.setattr("linkedin_easy_apply.llm.generate_json", fake_generate_json)
    result = classify_question_with_llm(row, store, use_llm=True)
    assert result["cleaned_question"] == "City of residence"
    stored = store.get_question(row["id"])
    assert stored is not None
    assert stored["cleaned_question"] == "City of residence"
    assert stored["classification_status"] == "llm"
    assert stored["approved_value"] == ""
    store.close()


def test_list_questions_hides_duplicates_and_resume(tmp_path):
    store = Store(str(tmp_path / "hide.sqlite"))
    store.upsert_question(raw_question="Email address", field_kind="text")
    store.upsert_question(raw_question="Email", field_kind="email")
    store.upsert_question(raw_question="Please upload your resume", field_kind="text")
    store.upsert_question(
        raw_question="How many years of SQL experience do you have?",
        field_kind="text",
    )
    pending = store.list_questions(status="pending")
    texts = {row["cleaned_question"] or row["raw_question"] for row in pending}
    kinds = {row["classified_kind"] for row in pending}
    assert len(pending) == 2
    assert "Email" in texts
    assert any("sql" in item.lower() for item in texts)
    assert "file" not in kinds
    assert all(not row.get("merged_into_id") for row in pending)
    counts = store.question_counts()
    assert counts["pending"] == 2
    store.close()


def test_years_and_yes_no_heuristics():
    years = heuristic_classify("How many years of Python experience?", "text")
    assert years["kind"] == "number"
    assert years["answer_shape"] == "number"
    yes_no = heuristic_classify(
        "Are you legally authorized to work in the United States?",
        "select",
        ["Yes", "No"],
    )
    assert yes_no["kind"] == "yes_no"
    phone = heuristic_classify("Mobile phone number", "text")
    assert phone["kind"] == "tel"


def test_classify_pending_uses_heuristics_when_ollama_down(tmp_path, monkeypatch):
    monkeypatch.setattr("linkedin_easy_apply.llm.status", lambda cfg=None: {"ready": False})

    def boom(*_args, **_kwargs):
        raise AssertionError("generate_json must not run when Ollama is down")

    monkeypatch.setattr("linkedin_easy_apply.llm.generate_json", boom)
    store = Store(str(tmp_path / "pending.sqlite"))
    store.upsert_question(raw_question="E-mail", field_kind="text")
    payload = classify_pending_questions(store)
    assert payload["ok"] is True
    assert payload["ollama_ready"] is False
    row = store.list_questions(status="pending")[0]
    assert row["classified_kind"] == "email"
    store.close()
