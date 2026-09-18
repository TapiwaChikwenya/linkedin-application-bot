from datetime import datetime, timedelta, timezone
from pathlib import Path

from linkedin_easy_apply.history import record_outcome
from linkedin_easy_apply.job_metrics import (
    classify_job,
    classify_taxonomy,
    outcome_from_status,
    parse_search_card_html,
    search_card_metrics,
)
from linkedin_easy_apply.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "job_search_cards.html"


def test_taxonomy_keywords():
    hospital = classify_taxonomy("Registered Nurse", "City Hospital", "", "Dallas, TX")
    assert hospital.sector == "healthcare"
    assert hospital.market == "hospital"
    assert hospital.location_text == "Dallas, TX"

    health = classify_taxonomy("Clinical Analyst", "UnitedHealth", "patient outcomes")
    assert health.sector == "healthcare"
    assert health.market == "health"

    bank = classify_taxonomy("Software Engineer", "JPMorgan Chase")
    assert bank.sector == "finance"
    assert bank.market == "bank"

    fintech = classify_taxonomy("Backend Engineer", "Acme Fintech Payments")
    assert fintech.sector == "finance"
    assert fintech.market == "fintech"

    insurance = classify_taxonomy("Actuarial Analyst", "Harbor Insurance")
    assert insurance.sector == "insurance"
    assert insurance.market == "insurance"

    government = classify_taxonomy("Data Analyst", "Department of Defense")
    assert government.sector == "government"
    assert government.market == "government"

    staffing = classify_taxonomy("Data Engineer", "Robert Half")
    assert staffing.sector == "staffing"
    assert staffing.market == "staffing"

    recruiting = classify_taxonomy("Technical Recruiter", "Hidden Corp")
    assert recruiting.sector == "staffing"
    assert recruiting.market == "recruiting"

    software = classify_taxonomy("Software Engineer", "Acme")
    assert software.sector == "technology"
    assert software.market == "software"

    data = classify_taxonomy("Senior Data Engineer", "Northwind")
    assert data.sector == "technology"
    assert data.market == "data"

    unknown = classify_taxonomy("Mystery Role", "Obscure LLC")
    assert unknown.sector == "other"
    assert unknown.market == "other"
    assert unknown.source == "taxonomy"


def test_outcome_from_status_groups_skips():
    assert outcome_from_status("applied") == "applied"
    assert outcome_from_status("skipped_filter") == "skipped"
    assert outcome_from_status("skipped_fit") == "skipped"
    assert outcome_from_status("already_applied") == "skipped"
    assert outcome_from_status("page_timeout") == "skipped"
    assert outcome_from_status("failed") == "failed"
    assert outcome_from_status("seen") == "seen"


def test_llm_classify_only_when_page_already_open():
    called = {"n": 0}

    def fake_generate(*_args, **_kwargs):
        called["n"] += 1
        return {"sector": "finance", "market": "bank"}

    closed = classify_job(
        "Mystery Role",
        "Obscure LLC",
        already_open=False,
        generate_json=fake_generate,
    )
    assert closed.sector == "other"
    assert closed.source == "taxonomy"
    assert called["n"] == 0

    known = classify_job(
        "Senior Data Engineer",
        "Acme",
        already_open=True,
        generate_json=fake_generate,
    )
    assert known.sector == "technology"
    assert known.source == "taxonomy"
    assert called["n"] == 0

    opened = classify_job(
        "Mystery Role",
        "Obscure LLC",
        snippet="We ship widgets to warehouses worldwide.",
        already_open=True,
        generate_json=fake_generate,
        status="applied",
    )
    assert opened.sector == "finance"
    assert opened.market == "bank"
    assert opened.source == "llm"
    assert opened.outcome == "applied"
    assert called["n"] == 1


def test_llm_garbage_keeps_taxonomy_other():
    result = classify_job(
        "Mystery Role",
        "Obscure LLC",
        already_open=True,
        generate_json=lambda *_args, **_kwargs: {},
    )
    assert result.sector == "other"
    assert result.source == "taxonomy"


def test_search_card_html_classifies_without_opening():
    from linkedin_easy_apply.job_card import parse_search_cards

    cards = parse_search_cards(FIXTURE.read_text(encoding="utf-8"))
    first = cards[0]
    parsed = parse_search_card_html(
        FIXTURE.read_text(encoding="utf-8").split("</li>", 1)[0] + "</li>"
    )
    assert parsed["job_id"] == "4461000001"
    assert first.title == "Senior Data Engineer"
    metrics = search_card_metrics(
        job_id=first.job_id,
        title=first.title,
        company=first.company,
        location=first.location,
    )
    assert metrics["sector"] == "technology"
    assert metrics["market"] == "data"
    assert metrics["page_open"] == "false"


def test_upsert_and_skip_event_persist_metrics(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    run_id = store.start_run("metrics-run")
    record_outcome(
        store,
        run_id,
        url="https://www.linkedin.com/jobs/view/111",
        title="Registered Nurse",
        company="City Hospital",
        location="Austin, TX",
        status="skipped_filter",
        reason="Skipped before open: title filter",
        already_open=False,
    )
    job = store.get_job("111")
    assert job is not None
    assert job["sector"] == "healthcare"
    assert job["market"] == "hospital"
    assert job["location_text"] == "Austin, TX"
    assert job["outcome"] == "skipped"
    events = store.list_events(run_id)
    payload = events[0]["payload_json"] or ""
    assert "healthcare" in payload
    assert "hospital" in payload
    assert "skipped" in payload
    store.close()


def test_jobs_schema_backfill_from_title_company(tmp_path):
    path = tmp_path / "legacy.sqlite"
    import sqlite3

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
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            stopped_at TEXT,
            status TEXT NOT NULL,
            applied_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            created_at TEXT NOT NULL,
            level TEXT NOT NULL,
            job_id TEXT,
            message TEXT NOT NULL,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO jobs(job_id, title, company, location, status, reason, seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "222",
            "Senior Data Engineer",
            "Acme",
            "Remote",
            "applied",
            "applied",
            "2026-09-17T12:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    store = Store(str(path))
    row = store.get_job("222")
    assert row is not None
    assert row["sector"] == "technology"
    assert row["market"] == "data"
    assert row["outcome"] == "applied"
    assert row["location_text"] == "Remote"
    store.close()


def test_job_metrics_sql_windows(tmp_path):
    store = Store(str(tmp_path / "metrics.sqlite"))
    now = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    store.upsert_job(
        "1",
        title="Senior Data Engineer",
        company="Acme",
        location="United States",
        status="applied",
    )
    store.upsert_job(
        "2",
        title="Technical Recruiter",
        company="Insight Global",
        location="Remote",
        status="skipped_filter",
    )
    store.upsert_job(
        "3",
        title="Registered Nurse",
        company="City Hospital",
        location="Dallas, TX",
        status="failed",
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE jobs SET seen_at = ? WHERE job_id = ?",
            ((now - timedelta(days=3)).isoformat(timespec="seconds"), "2"),
        )
        conn.execute(
            "UPDATE jobs SET seen_at = ? WHERE job_id = ?",
            ((now - timedelta(days=10)).isoformat(timespec="seconds"), "3"),
        )
        conn.execute(
            "UPDATE jobs SET seen_at = ? WHERE job_id = ?",
            (now.isoformat(timespec="seconds"), "1"),
        )
    payload = store.job_metrics(now=now)
    assert payload["source"] == "store"
    assert payload["today"]["totals"]["applied"] == 1
    assert payload["today"]["totals"]["total"] == 1
    week = payload["last_7_days"]["totals"]
    assert week["applied"] == 1
    assert week["skipped"] == 1
    assert week["total"] == 2
    sectors = {item["key"]: item for item in payload["last_7_days"]["by_sector"]}
    assert sectors["technology"]["applied"] == 1
    assert sectors["staffing"]["skipped"] == 1
    markets = {item["key"]: item for item in payload["last_7_days"]["by_market"]}
    assert markets["data"]["applied"] == 1
    locations = {item["key"]: item for item in payload["today"]["by_location"]}
    assert locations["United States"]["applied"] == 1
    assert "hospital" in payload["taxonomy"]["markets"]
    store.close()
