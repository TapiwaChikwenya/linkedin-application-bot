from datetime import datetime

from linkedin_easy_apply.operator_settings import (
    normalize_windows,
    schedule_status,
)
from linkedin_easy_apply.quota import record_application, remaining_applications
from linkedin_easy_apply.scheduler import RunScheduler
from linkedin_easy_apply.store import Store


def test_remaining_applications_uses_operator_override(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.set_setting("max_applications_per_day", "4")
    store.set_setting("max_applications_per_run", "10")
    quota_path = tmp_path / "quota.json"
    record_application(str(quota_path))
    assert remaining_applications(0, 12, 25, path=str(quota_path), store=store) == 3


def test_schedule_window_includes_weekday_noon():
    windows = normalize_windows(
        [{"days": [4], "start": "09:00", "end": "17:00"}]
    )
    now = datetime(2026, 9, 18, 12, 0)
    status = schedule_status(now=now, windows=windows)
    assert status["enabled"] is True
    assert status["active"] is True
    assert status["reason"] == "inside schedule"


def test_schedule_window_excludes_evening_and_weekend():
    windows = normalize_windows(
        [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}]
    )
    friday_evening = schedule_status(
        now=datetime(2026, 9, 18, 20, 0),
        windows=windows,
    )
    saturday = schedule_status(
        now=datetime(2026, 9, 19, 12, 0),
        windows=windows,
    )
    assert friday_evening["enabled"] is True
    assert friday_evening["active"] is False
    assert friday_evening["reason"] == "outside schedule"
    assert saturday["active"] is False


def test_empty_schedule_is_manual_only():
    status = schedule_status(now=datetime(2026, 9, 18, 12, 0), windows=[])
    assert status["enabled"] is False
    assert status["active"] is True
    assert "manual" in status["reason"]


def test_all_day_window_covers_local_calendar_day():
    windows = normalize_windows([{"days": [4], "start": "00:00", "end": "23:59"}])
    status = schedule_status(now=datetime(2026, 9, 18, 23, 30), windows=windows)
    assert status["active"] is True


def test_scheduler_starts_only_at_window_open(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.set_setting(
        "schedule_windows",
        '[{"days":[4],"start":"09:00","end":"17:00"}]',
    )
    calls = {"start": 0, "stop": 0}
    alive = {"value": False}

    scheduler = RunScheduler(
        store,
        start_run=lambda: calls.__setitem__("start", calls["start"] + 1) or {"ok": True},
        stop_run=lambda: calls.__setitem__("stop", calls["stop"] + 1),
        is_alive=lambda: alive["value"],
    )
    inside = scheduler.tick(now=datetime(2026, 9, 18, 10, 0))
    assert inside["action"] == "start"
    assert calls["start"] == 1
    alive["value"] = True
    again = scheduler.tick(now=datetime(2026, 9, 18, 11, 0))
    assert again["action"] == "idle"
    assert calls["start"] == 1
    alive["value"] = True
    closed = scheduler.tick(now=datetime(2026, 9, 18, 18, 0))
    assert closed["action"] == "stop"
    assert calls["stop"] == 1
