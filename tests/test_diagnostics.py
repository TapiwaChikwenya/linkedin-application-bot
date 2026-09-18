from datetime import datetime, timezone

from linkedin_easy_apply.diagnostics import artifact_stem, sanitize_html


def test_artifact_stem_is_job_and_time_specific():
    first = artifact_stem(
        "easy_apply_failure",
        "job/123",
        datetime(2026, 9, 17, 10, 11, 12, 123456, tzinfo=timezone.utc),
    )
    second = artifact_stem(
        "easy_apply_failure",
        "job/123",
        datetime(2026, 9, 17, 10, 11, 12, 123457, tzinfo=timezone.utc),
    )
    assert first == "easy_apply_failure_job_123_20260917T101112123456Z"
    assert first != second


def test_diagnostic_html_redacts_contact_values():
    html = """
    <input type="email" value="person@example.com">
    <input type="tel" value="+1 (555) 123-4567">
    <textarea>private response</textarea>
    <div>person@example.com / 555-123-4567</div>
    """
    cleaned = sanitize_html(html)
    assert "person@example.com" not in cleaned
    assert "555-123-4567" not in cleaned
    assert "private response" not in cleaned
    assert "[REDACTED]" in cleaned
