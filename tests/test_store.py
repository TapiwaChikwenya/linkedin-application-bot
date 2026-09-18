from linkedin_easy_apply.store import (
    Store,
    extract_job_id,
    normalize_question_key,
    status_from_result,
)


def test_extract_job_id():
    assert extract_job_id("https://www.linkedin.com/jobs/view/4466303632") == "4466303632"
    assert extract_job_id("nope") == ""


def test_status_from_result():
    assert status_from_result("* Just Applied to this job: https://x") == "applied"
    assert status_from_result("Skipped by title/company filter") == "skipped_filter"
    assert status_from_result("Job page did not finish loading") == "page_timeout"
    assert status_from_result("Easy Apply unavailable or already applied") == "already_applied"
    assert status_from_result("Job fit llama=0.21 skip: staffing recruiter") == "skipped_fit"
    assert status_from_result("* Skipped: Unanswered required question: Favorite color?") == "needs_review"
    assert status_from_result("Already applied") == "already_applied"
    assert status_from_result("Couldn't apply: extra information") == "failed"
    assert status_from_result("Easy Apply control not found") == "failed"
    assert status_from_result("Off-site apply, not Easy Apply") == "failed"
    assert status_from_result("Search card title missing; skipped without opening") == (
        "skipped_filter"
    )
    assert status_from_result("No Easy Apply on search card") == "failed"
    assert status_from_result("Skipped before open: intern. Job: https://www.linkedin.com/jobs/view/1") == "skipped_filter"
    assert status_from_result("Skipped before open: already applied. Job: https://x") == "already_applied"
    assert status_from_result("Skipped before open: no Easy Apply. Job: https://x") == "failed"


def test_store_round_trip(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("run1")
    store.upsert_job(
        "4466303632",
        title="Senior Data Engineer",
        company="Acme",
        url="https://www.linkedin.com/jobs/view/4466303632",
        status="applied",
        reason="applied",
        run_id=run_id,
    )
    store.add_event(run_id, "info", "4466303632", "applied")
    jobs = store.list_jobs(query="Acme")
    assert len(jobs) == 1
    assert jobs[0]["status"] == "applied"
    stored = store.get_job("4466303632")
    assert stored is not None
    assert stored["status"] == "applied"
    assert store.get_job("missing") is None
    store.upsert_job(
        "4466303633",
        title="Recruiter",
        company="Insight Global",
        status="skipped_filter",
        reason="title filter",
        run_id=run_id,
    )
    skipped = store.list_jobs(status="skipped")
    assert [job["job_id"] for job in skipped] == ["4466303633"]
    assert store.count_jobs(run_id, "applied") == 1
    store.finish_run(run_id, "completed")
    assert store.current_run()["status"] == "completed"
    store.close()


def test_normalize_question_key():
    assert normalize_question_key("  Years of  SQL? ") == normalize_question_key("years of SQL?")


def test_questions_record_list_approve_reject(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    first = store.record_question(
        "Years of SQL?",
        kind="number",
        options=[],
        proposed_value="9",
        job_id="111",
        job_title="Data Engineer",
        job_company="Northwind",
    )
    assert first["status"] == "pending"
    assert first["seen_count"] == 1
    again = store.record_question(
        "years of  SQL?",
        kind="number",
        proposed_value="8",
        job_id="222",
        job_title="Azure Engineer",
        job_company="Contoso",
    )
    assert again["seen_count"] == 2
    assert again["proposed_value"] == "8"
    assert again["last_job_title"] == "Azure Engineer"
    pending = store.list_questions(status="pending", query="SQL", kind="number")
    assert len(pending) == 1
    approved = store.approve_question(first["question_id"], "9")
    assert approved is not None
    assert approved["status"] == "approved"
    assert approved["approved_value"] == "9"
    assert store.list_questions(status="pending") == []
    rejected = store.reject_question("Work authorization?")
    assert rejected is None
    store.record_question("Sponsorship required?", kind="select", options=["Yes", "No"])
    denied = store.reject_question("Sponsorship required?")
    assert denied is not None
    assert denied["status"] == "rejected"
    counts = store.question_counts()
    assert counts["approved"] == 1
    assert counts["rejected"] == 1
    assert counts["pending"] == 0
    store.close()


def test_schema_tables_omit_outcome_index():
    from linkedin_easy_apply.store import JOBS_INDEXES, SCHEMA, SCHEMA_TABLES

    assert "idx_jobs_seen_outcome" not in SCHEMA
    assert "idx_jobs_seen_outcome" not in SCHEMA_TABLES
    assert "outcome TEXT" in SCHEMA_TABLES
    assert any("idx_jobs_seen_outcome" in sql for _col, sql in JOBS_INDEXES)


def test_store_initializes_legacy_jobs_table_without_outcome(tmp_path):
    import sqlite3

    from linkedin_easy_apply.store import Store

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
    assert "sector" in cols
    assert "market" in cols
    with store.connection() as db:
        indexes = [
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"
            ).fetchall()
        ]
    assert "idx_jobs_seen_outcome" in indexes
    row = store.get_job("1")
    assert row is not None
    store.upsert_job("1", title="Data Engineer", status="applied")
    assert store.get_job("1")["status"] == "applied"
    store.close()


def test_approve_question_succeeds_while_writer_holds_db_briefly(tmp_path):
    import sqlite3
    import threading
    import time

    store = Store(str(tmp_path / "busy.sqlite"))
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

    def do_approve() -> None:
        result["row"] = store.approve_question(row["id"], "9")

    thread = threading.Thread(target=hold)
    thread.start()
    assert holding.wait(timeout=2)
    approve_thread = threading.Thread(target=do_approve)
    approve_thread.start()
    time.sleep(0.15)
    release.set()
    approve_thread.join(timeout=3)
    thread.join(timeout=3)
    approved = result.get("row")
    assert isinstance(approved, dict)
    assert approved["status"] == "approved"
    assert approved["approved_value"] == "9"
    store.close()


def test_approve_empty_value_raises(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.record_question("Authorized to work?", kind="select", options=["Yes", "No"])
    try:
        store.approve_question("Authorized to work?", "  ")
    except ValueError as exc:
        assert "approved_value" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    store.close()


def test_retrieve_approved_exact_then_conservative_overlap(tmp_path):
    from linkedin_easy_apply.store import match_approved_answers

    store = Store(str(tmp_path / "retrieve.sqlite"))
    sql = store.upsert_question(
        raw_question="How many years of SQL experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="9",
    )
    store.approve_answer(sql["id"], "9")
    python = store.upsert_question(
        raw_question="How many years of Python experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="4",
    )
    store.approve_answer(python["id"], "4")
    airflow = store.upsert_question(
        raw_question="How many years of Apache Airflow DAG experience do you have?",
        field_kind="number",
        source="manual",
        proposed_value="6",
    )
    store.approve_answer(airflow["id"], "6")

    exact = store.retrieve_approved_answers([
        {"question": "How many years of SQL experience do you have?"},
    ])
    assert exact[0]["match"] == "exact"
    assert exact[0]["value"] == "9"
    assert exact[0]["source_id"] == str(sql["id"])

    overlap = store.retrieve_approved_answers([
        {"question": "How many years of Apache Airflow DAG experience?"},
    ])
    assert overlap[0]["match"] == "overlap"
    assert overlap[0]["value"] == "6"

    pool = store.list_approved_questions()
    weak = match_approved_answers(
        pool,
        [{"question": "How many years of Python experience do you have?"}],
    )
    assert [item["value"] for item in weak] == ["4"]
    skipped = match_approved_answers(
        [row for row in pool if row["id"] == sql["id"]],
        [{"question": "How many years of Python experience do you have?"}],
    )
    assert skipped == []
    store.close()
