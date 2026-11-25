from linkedin_easy_apply.importer import (
    applied_stamp_from_log_path,
    import_text_logs,
    parse_log_line,
)
from linkedin_easy_apply.quota import applications_today
from linkedin_easy_apply.store import Store, local_date_of

SAMPLE = """---- Applied Jobs Data ---- created at: 20260917
---- Number | Job Title | Company | Location | Work Place | Posted Date | Applications | Result
 Category: Senior Data Engineer, Location: United States, Applying unknown jobs.
1 |  |  |  |  |  |  | * Skipped by title/company filter. Job: https://www.linkedin.com/jobs/view/4466303632
12 | Senior Data Engineer | Acme | Remote | Remote | 1 day ago |  | * Just Applied to this job: https://www.linkedin.com/jobs/view/1111111111
"""


def test_parse_skip_and_apply_lines():
    skip = parse_log_line(
        "1 |  |  |  |  |  |  | * Skipped by title/company filter. Job: https://www.linkedin.com/jobs/view/4466303632"
    )
    assert skip is not None
    assert skip["job_id"] == "4466303632"
    assert skip["status"] == "skipped_filter"
    applied = parse_log_line(
        "12 | Senior Data Engineer | Acme | Remote | Remote | 1 day ago |  | * Just Applied to this job: https://www.linkedin.com/jobs/view/1111111111"
    )
    assert applied is not None
    assert applied["title"] == "Senior Data Engineer"
    assert applied["company"] == "Acme"
    assert applied["status"] == "applied"


def test_parse_skips_headers():
    assert parse_log_line("---- Applied Jobs Data ----") is None
    assert parse_log_line("Category: Senior Data Engineer, Location: United States, Applying 10 jobs.") is None


def test_import_text_logs(tmp_path):
    log = tmp_path / "Applied Jobs DATA - 20260917.txt"
    log.write_text(SAMPLE, encoding="utf-8")
    store = Store(str(tmp_path / "assistant.sqlite"))
    imported = import_text_logs(store, str(tmp_path))
    assert imported == 2
    jobs = store.list_jobs()
    ids = {job["job_id"] for job in jobs}
    assert ids == {"4466303632", "1111111111"}
    applied = next(job for job in jobs if job["job_id"] == "1111111111")
    assert local_date_of(applied["applied_at"]) == "2026-09-17"
    assert applications_today(str(tmp_path / "quota.json"), store=store) == 0
    store.close()


def test_reimport_does_not_move_an_existing_apply_onto_today(tmp_path):
    log = tmp_path / "Applied Jobs DATA - 20260917.txt"
    log.write_text(SAMPLE, encoding="utf-8")
    store = Store(str(tmp_path / "assistant.sqlite"))
    import_text_logs(store, str(tmp_path))
    first = store.get_job("1111111111")["applied_at"]
    import_text_logs(store, str(tmp_path))
    assert store.get_job("1111111111")["applied_at"] == first
    assert applied_stamp_from_log_path(str(log))
    store.close()
