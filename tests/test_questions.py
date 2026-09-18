import sqlite3

from linkedin_easy_apply.question_capture import (
    classify_source,
    normalize_question,
    option_set_hash,
    redact_proposed_value,
    remember_questions,
    should_skip_field,
)
from linkedin_easy_apply.store import Store, normalize_question_key


def test_normalize_question_folds_case_dashes_and_punctuation():
    assert normalize_question("  How many years of SQL experience? ") == (
        "how many years of sql experience?"
    )
    assert normalize_question("Work\u2011authorization") == "work-authorization"
    assert normalize_question_key("SQL Years:") == normalize_question("sql years")


def test_option_set_hash_differs_when_select_choices_differ():
    yes_no = option_set_hash("select", ["Select an option", "Yes", "No"])
    extra = option_set_hash("select", ["Yes", "No", "Prefer not to say"])
    assert yes_no
    assert extra
    assert yes_no != extra
    assert option_set_hash("select", ["Select an option", "[email]"]) == ""


def test_redact_and_skip_sensitive_fields():
    assert redact_proposed_value("email", "person@example.com") == "[redacted-email]"
    assert redact_proposed_value("tel", "5551234567") == "[redacted-phone]"
    assert redact_proposed_value("text", "Yes") == "Yes"
    assert should_skip_field({"kind": "file", "question": "Resume"}) is True
    assert should_skip_field({"kind": "password", "question": "Password"}) is True
    assert should_skip_field({"kind": "text", "type": "password", "question": "Secret"}) is True
    assert should_skip_field({"kind": "text", "question": "Favorite color?"}) is False


def test_upsert_increments_seen_count_and_keeps_first_raw(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    first = store.upsert_question(
        raw_question="How many years of SQL experience?",
        field_kind="number",
        source="mapped",
        proposed_value="9",
        job_id="1",
        company="Acme",
        title="Data Engineer",
    )
    second = store.upsert_question(
        raw_question="how many years of SQL experience?",
        field_kind="number",
        source="unanswered",
        proposed_value="",
        job_id="2",
        company="Other",
        title="Analyst",
    )
    assert first is not None and second is not None
    assert first["id"] == second["id"]
    assert second["seen_count"] == 2
    assert second["raw_question"] == "How many years of SQL experience?"
    assert second["source"] == "mapped"
    assert second["proposed_value"] == "9"
    assert second["job_id"] == "2"
    store.close()


def test_pending_versus_approved_answers(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    row = store.upsert_question(
        raw_question="Will you require sponsorship?",
        field_kind="select",
        options=["Yes", "No"],
        source="unanswered",
        required=True,
    )
    assert row is not None
    pending = store.list_pending_questions()
    assert [item["id"] for item in pending] == [row["id"]]
    assert store.list_approved_questions() == []
    approved = store.approve_answer(row["id"], "No")
    assert approved["approval_status"] == "approved"
    assert approved["approved_value"] == "No"
    assert approved["source"] == "manual"
    assert approved["provenance"] == "operator"
    assert store.list_pending_questions() == []
    remembered = store.list_approved_questions()
    assert len(remembered) == 1
    assert remembered[0]["approved_value"] == "No"
    store.close()


def test_option_set_variants_are_distinct_rows(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    first = store.upsert_question(
        raw_question="Are you authorized to work in the US?",
        field_kind="select",
        options=["Yes", "No"],
        source="mapped",
        proposed_value="Yes",
    )
    second = store.upsert_question(
        raw_question="Are you authorized to work in the US?",
        field_kind="select",
        options=["Yes", "No", "Prefer not to say"],
        source="unanswered",
    )
    assert first is not None and second is not None
    assert first["id"] != second["id"]
    assert first["option_set_hash"] != second["option_set_hash"]
    store.close()


def test_email_and_password_are_not_stored_raw(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    email = store.upsert_question(
        raw_question="Email address",
        field_kind="email",
        source="mapped",
        proposed_value="person@example.com",
    )
    skipped = store.upsert_question(
        raw_question="Password",
        field_kind="password",
        source="mapped",
        proposed_value="hunter2",
    )
    assert skipped is None
    assert email is not None
    assert email["proposed_value"] == "[redacted-email]"
    dumped = " ".join(str(value) for value in email.values())
    assert "person@example.com" not in dumped
    assert "hunter2" not in dumped
    store.close()


def test_remember_questions_classifies_mapped_llm_and_unanswered(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    count = remember_questions(
        store,
        [
            {
                "question": "How many years of SQL experience?",
                "kind": "number",
                "value": "9",
                "required": True,
                "options": [],
                "_mapped_value_present": True,
                "_answered": True,
            },
            {
                "question": "Briefly describe your Airflow experience.",
                "kind": "textarea",
                "value": "Built Airflow DAGs for 6 years.",
                "options": [],
                "_llm_considered": True,
                "_llm_value": "Built Airflow DAGs for 6 years.",
                "_answered": True,
            },
            {
                "question": "Favorite color?",
                "kind": "text",
                "value": "",
                "required": True,
                "options": [],
            },
            {"question": "Resume", "kind": "file", "value": "", "options": []},
        ],
        job_id="4466303632",
        title="Senior Data Engineer",
        company="Acme",
    )
    assert count == 3
    by_question = {row["normalized_question"]: row for row in store.list_pending_questions()}
    assert by_question["how many years of sql experience?"]["source"] == "mapped"
    assert by_question["briefly describe your airflow experience."]["source"] == "llm"
    assert by_question["favorite color?"]["source"] == "unanswered"
    assert classify_source({"_mapped_value_present": True}) == "mapped"
    store.close()


def test_landed_questions_schema_is_not_rebuilt(tmp_path):
    path = tmp_path / "assistant.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_question TEXT NOT NULL,
            raw_question TEXT NOT NULL,
            field_kind TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            option_set_hash TEXT NOT NULL DEFAULT '',
            required INTEGER NOT NULL DEFAULT 0,
            job_id TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'unanswered',
            proposed_value TEXT NOT NULL DEFAULT '',
            approved_value TEXT NOT NULL DEFAULT '',
            approval_status TEXT NOT NULL DEFAULT 'pending',
            provenance TEXT NOT NULL DEFAULT '',
            confidence REAL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_questions_identity
            ON questions(normalized_question, field_kind, option_set_hash);
        """
    )
    conn.execute(
        """
        INSERT INTO questions(
            normalized_question, raw_question, field_kind, approved_value,
            approval_status, source, seen_count, last_seen_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "how many years of sql experience?",
            "How many years of SQL experience?",
            "number",
            "9",
            "approved",
            "manual",
            1,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    original_id = int(conn.execute("SELECT id FROM questions").fetchone()[0])
    conn.close()

    store = Store(str(path))
    approved = store.list_approved_questions()
    assert len(approved) == 1
    assert approved[0]["id"] == original_id
    assert approved[0]["approved_value"] == "9"
    assert "reuse_count" in store._table_columns("questions")
    with store.connection() as conn:
        names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'questions%'"
        ).fetchall()
    assert [row[0] for row in names] == ["questions"]
    row = store.upsert_question(
        raw_question="New leftover question?",
        field_kind="text",
        source="unanswered",
    )
    assert row is not None
    assert row["approval_status"] == "pending"
    store.close()


def test_fill_known_fields_persists_every_observed_question(monkeypatch, tmp_path):
    import config
    from linkedin import Linkedin

    store = Store(str(tmp_path / "assistant.sqlite"))
    monkeypatch.setattr(config, "application_city", "")
    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 5})
    monkeypatch.setattr(config, "yes_no_answers", [])
    monkeypatch.setattr(config, "phone_number", "5550001111")

    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.current_job_details = {"title": "Data Engineer", "company": "Acme"}
    bot.observe = lambda *args, **kwargs: None
    fields = [
        {
            "question": "How many years of SQL experience do you have?",
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
        {"question": "Phone", "kind": "tel", "value": "", "options": []},
        {"question": "Password", "kind": "password", "value": "secret", "options": []},
        {"question": "Resume", "kind": "file", "value": "", "options": []},
    ]
    bot.collect_form_state = lambda dialog: (fields, "<html>full page should not be stored</html>")
    bot.attachResume = lambda dialog: None
    bot.fill_control = lambda field, value: True
    bot.fill_llm_fields = lambda *args, **kwargs: None
    monkeypatch.setattr(
        "linkedin_easy_apply.question_capture.append_questions",
        lambda *args, **kwargs: 0,
    )

    bot.fillKnownFields(None, "4466303632")

    pending = store.list_pending_questions()
    kinds = {row["field_kind"] for row in pending}
    questions = {row["normalized_question"] for row in pending}
    assert "file" not in kinds
    assert "password" not in kinds
    assert "how many years of sql experience do you have?" in questions
    assert "what is your favorite color?" in questions
    phone = next(row for row in pending if row["field_kind"] == "tel")
    assert phone["proposed_value"] == "[redacted-phone]"
    blob = " ".join(str(value) for row in pending for value in row.values())
    assert "secret" not in blob
    assert "<html>" not in blob
    sql = next(row for row in pending if "sql" in row["normalized_question"])
    assert sql["source"] == "mapped"
    store.close()


def test_fill_known_fields_skips_job_when_required_question_unanswered(monkeypatch, tmp_path):
    import config
    from linkedin import Linkedin

    store = Store(str(tmp_path / "skip-required.sqlite"))
    monkeypatch.setattr(config, "application_city", "")
    monkeypatch.setattr(config, "years_experience", {"sql": 9})
    monkeypatch.setattr(config, "yes_no_answers", [])
    monkeypatch.setattr(config, "phone_number", "")

    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.current_job_details = {"title": "Data Engineer", "company": "Acme"}
    bot.observe = lambda *args, **kwargs: None
    fields = [
        {
            "question": "How many years of SQL experience do you have?",
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
    ]
    bot.collect_form_state = lambda dialog: (fields, "form")
    bot.attachResume = lambda dialog: None
    bot.fill_control = lambda field, value: True
    bot.fill_llm_fields = lambda *args, **kwargs: None

    reason = bot.fillKnownFields(None, "4466303632")
    assert reason == "Unanswered required question: What is your favorite color?"
    store.close()


def test_fill_known_fields_uses_approved_memory_before_mapped_config(monkeypatch, tmp_path):
    import config
    from linkedin import Linkedin

    store = Store(str(tmp_path / "assistant.sqlite"))
    row = store.upsert_question(
        raw_question="How many years of SQL experience do you have?",
        field_kind="number",
        source="manual",
    )
    store.approve_answer(row["id"], "4")
    monkeypatch.setattr(config, "application_city", "")
    monkeypatch.setattr(config, "years_experience", {"sql": 9, "default": 5})
    monkeypatch.setattr(config, "yes_no_answers", [(("sponsorship",), "No")])
    monkeypatch.setattr(config, "phone_number", "")

    filled = []
    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.current_job_details = {"title": "Data Engineer", "company": "Acme"}
    bot.observe = lambda *args, **kwargs: None
    fields = [
        {
            "question": "How many years of SQL experience do you have?",
            "kind": "number",
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

    bot.fillKnownFields(None, "4466303632")

    assert filled[0] == ("How many years of SQL experience do you have?", "4")
    assert filled[1] == ("Will you require sponsorship?", "No")
    sql = store.get_question(row["id"])
    assert sql["approved_value"] == "4"
    remembered = next(
        item for item in store.list_questions(limit=50)
        if "sql" in item["normalized_question"]
    )
    assert remembered["source"] == "manual"
    store.close()


def test_fill_known_fields_upserts_llm_fills_as_pending(monkeypatch, tmp_path):
    import config
    from linkedin import Linkedin

    store = Store(str(tmp_path / "assistant.sqlite"))
    monkeypatch.setattr(config, "application_city", "")
    monkeypatch.setattr(config, "years_experience", {})
    monkeypatch.setattr(config, "yes_no_answers", [])
    monkeypatch.setattr(config, "phone_number", "")

    def fake_llm(fields, snapshot, job_id=None):
        for field in fields:
            field["_llm_considered"] = True
            field["_llm_value"] = "No"
            field["_answered"] = True
            field["value"] = "No"

    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.current_job_details = {"title": "Data Engineer", "company": "Acme"}
    bot.observe = lambda *args, **kwargs: None
    fields = [{
        "question": "Will you require sponsorship?",
        "kind": "select",
        "options": ["Yes", "No"],
        "value": "",
        "required": True,
    }]
    bot.collect_form_state = lambda dialog: (fields, "form")
    bot.attachResume = lambda dialog: None
    bot.fill_control = lambda field, value: True
    bot.fill_llm_fields = fake_llm

    bot.fillKnownFields(None, "4466303632")

    pending = store.list_pending_questions()
    assert len(pending) == 1
    assert pending[0]["source"] == "llm"
    assert pending[0]["approval_status"] == "pending"
    assert pending[0]["proposed_value"] == "No"
    store.close()
