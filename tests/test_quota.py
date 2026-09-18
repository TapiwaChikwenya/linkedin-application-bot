from linkedin_easy_apply.quota import (
    applications_today,
    load_quota,
    record_application,
    remaining_applications,
)


def test_quota_resets_on_a_new_day(tmp_path):
    path = tmp_path / "quota.json"
    path.write_text('{"date": "2000-01-01", "count": 20}', encoding="utf-8")
    payload = load_quota(str(path))
    assert payload["count"] == 0


def test_record_application_increments(tmp_path):
    path = tmp_path / "quota.json"
    assert record_application(str(path)) == 1
    assert record_application(str(path)) == 2
    assert applications_today(str(path)) == 2


def test_remaining_applications_uses_the_tighter_cap(tmp_path):
    path = tmp_path / "quota.json"
    record_application(str(path))
    record_application(str(path))
    assert remaining_applications(0, max_per_run=12, max_per_day=3, path=str(path)) == 1
    assert remaining_applications(12, max_per_run=12, max_per_day=25, path=str(path)) == 0
