from pathlib import Path

from linkedin_easy_apply.runlog import configure_logging, daily_log_path, event


def test_daily_log_keeps_unicode_job_lines(tmp_path):
    path = configure_logging(str(tmp_path), role="test")
    event("Couldn't apply \U0001f975 unanswered")
    text = Path(path).read_text(encoding="utf-8")
    assert "\U0001f975" in text
    assert "role=test" in text
    assert Path(path) == daily_log_path(str(tmp_path))
