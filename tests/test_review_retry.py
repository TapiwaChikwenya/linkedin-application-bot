"""needs_review jobs reopen only after the missing answer exists."""

from linkedin_easy_apply.review_retry import (
    question_from_review_reason,
    retryable_jobs,
)
from linkedin_easy_apply.store import Store


def test_question_is_taken_from_the_skip_reason():
    reason = "* Skipped: Unanswered required question: LinkedIn profile URL. Link: https://example"
    assert question_from_review_reason(reason) == "LinkedIn profile URL"


def test_retryable_after_approval_and_not_before(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "LinkedInProfileURL", "", raising=False)
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.upsert_job(
        "4490565483",
        title="Engineer",
        company="Simbe",
        url="https://www.linkedin.com/jobs/view/4490565483",
        status="needs_review",
        reason="Unanswered required question: LinkedIn",
    )
    assert retryable_jobs(store, config) == []
    row = store.upsert_question(raw_question="LinkedIn", field_kind="text", job_id="4490565483")
    assert row is not None
    store.approve_question(row["id"], "https://www.linkedin.com/in/example")
    ready = retryable_jobs(store, config)
    assert [item["job_id"] for item in ready] == ["4490565483"]


def test_month_retry_waits_for_work_history(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "work_history_month", "", raising=False)
    monkeypatch.setattr(config, "work_history", [], raising=False)
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.upsert_job(
        "4482925672",
        title="Engineer",
        company="Crossing",
        status="needs_review",
        reason="Unanswered required question: month",
    )
    assert retryable_jobs(store, config) == []
    store.set_setting(
        "work_history",
        '[{"company":"Example","title":"Engineer","start_month":"March","start_year":"2019","current":true}]',
    )
    assert [item["job_id"] for item in retryable_jobs(store, config)] == ["4482925672"]
