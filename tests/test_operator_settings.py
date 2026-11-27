from datetime import datetime, timezone

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
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    status = schedule_status(now=now, windows=windows)
    assert status["enabled"] is True
    assert status["active"] is True
    assert status["reason"] == "inside schedule"


def test_schedule_window_excludes_evening_and_weekend():
    windows = normalize_windows(
        [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}]
    )
    friday_evening = schedule_status(
        now=datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc),
        windows=windows,
    )
    saturday = schedule_status(
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
        windows=windows,
    )
    assert friday_evening["enabled"] is True
    assert friday_evening["active"] is False
    assert friday_evening["reason"] == "outside schedule"
    assert saturday["active"] is False


def test_overnight_window_includes_the_next_morning_as_one_session():
    windows = normalize_windows([{"days": [4], "start": "22:00", "end": "06:00"}])
    friday_night = schedule_status(
        now=datetime(2026, 9, 18, 23, 15, tzinfo=timezone.utc),
        windows=windows,
    )
    saturday_morning = schedule_status(
        now=datetime(2026, 9, 19, 5, 30, tzinfo=timezone.utc),
        windows=windows,
    )
    saturday_after = schedule_status(
        now=datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc),
        windows=windows,
    )
    assert friday_night["active"] is True
    assert saturday_morning["active"] is True
    assert friday_night["window_key"].startswith("2026-09-18|")
    assert saturday_morning["window_key"] == friday_night["window_key"]
    assert saturday_after["active"] is False


def test_overnight_morning_does_not_use_the_next_listed_day():
    windows = normalize_windows([{"days": [5], "start": "22:00", "end": "06:00"}])
    saturday_morning = schedule_status(
        now=datetime(2026, 9, 19, 5, 30, tzinfo=timezone.utc),
        windows=windows,
    )
    saturday_night = schedule_status(
        now=datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc),
        windows=windows,
    )
    assert saturday_morning["active"] is False
    assert saturday_night["active"] is True


def test_empty_schedule_is_manual_only():
    status = schedule_status(now=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc), windows=[])
    assert status["enabled"] is False
    assert status["active"] is True
    assert "manual" in status["reason"]


def test_all_day_window_covers_local_calendar_day():
    windows = normalize_windows([{"days": [4], "start": "00:00", "end": "23:59"}])
    status = schedule_status(now=datetime(2026, 9, 18, 23, 30, tzinfo=timezone.utc), windows=windows)
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
    inside = scheduler.tick(now=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc))
    assert inside["action"] == "start"
    assert calls["start"] == 1
    alive["value"] = True
    again = scheduler.tick(now=datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc))
    assert again["action"] == "idle"
    assert calls["start"] == 1
    alive["value"] = True
    closed = scheduler.tick(now=datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc))
    assert closed["action"] == "stop"
    assert calls["stop"] == 1


def test_schedule_accepts_time_input_with_seconds():
    windows = normalize_windows([{"days": [0], "start": "09:00:00", "end": "17:00:00"}])
    assert windows[0]["start"] == "09:00"
    assert windows[0]["end"] == "17:00"


def _open_scheduler(tmp_path, **extra):
    store = Store(str(tmp_path / "assistant.sqlite"))
    store.set_setting(
        "schedule_windows",
        '[{"days":[4],"start":"09:00","end":"17:00"}]',
    )
    inside = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
    calls = {"start": 0, "stop": 0, "release": 0}
    alive = {"value": False}
    outcome = {"value": ""}

    def start():
        calls["start"] += 1
        outcome["value"] = "running"
        return {"ok": True}

    scheduler = RunScheduler(
        store,
        start_run=start,
        stop_run=lambda: calls.__setitem__("stop", calls["stop"] + 1),
        is_alive=lambda: alive["value"],
        clock=lambda: inside,
        release_ollama=lambda: calls.__setitem__("release", calls["release"] + 1),
        run_status=lambda: outcome["value"],
        **extra,
    )
    return scheduler, calls, alive, outcome, inside


def test_scheduler_retries_after_crash_inside_window(tmp_path):
    scheduler, calls, alive, outcome, inside = _open_scheduler(tmp_path)
    assert scheduler.tick(now=inside)["action"] == "start"
    alive["value"] = False
    outcome["value"] = "crashed"
    again = scheduler.tick(now=inside)
    assert again["action"] == "start"
    assert calls["start"] == 2
    assert calls["release"] == 0


def test_scheduler_stops_ollama_when_run_finishes_inside_window(tmp_path):
    scheduler, calls, alive, outcome, inside = _open_scheduler(tmp_path)
    scheduler.tick(now=inside)
    alive["value"] = True
    scheduler.tick(now=inside)
    alive["value"] = False
    outcome["value"] = "completed"
    done = scheduler.tick(now=inside)
    assert done["reason"] == "run finished"
    assert calls["start"] == 1
    assert calls["release"] == 1
    scheduler.tick(now=inside)
    assert calls["release"] == 1
    assert calls["start"] == 1


def test_scheduler_does_not_restart_after_operator_stop(tmp_path):
    scheduler, calls, _alive, _outcome, inside = _open_scheduler(tmp_path)
    scheduler.tick(now=inside)
    scheduler.note_operator_stop()
    held = scheduler.tick(now=inside)
    assert held["reason"] == "stopped by operator"
    assert calls["start"] == 1
    scheduler.clear_operator_hold()
    resumed = scheduler.tick(now=inside)
    assert resumed["action"] == "start"
    assert calls["start"] == 2


def test_scheduler_loop_ticks_before_waiting(tmp_path):
    scheduler, calls, _alive, _outcome, _inside = _open_scheduler(tmp_path)

    def tick(now=None):
        calls["ticks"] = calls.get("ticks", 0) + 1
        scheduler.stop()
        return {"ok": True, "action": "idle"}

    scheduler.tick = tick
    scheduler._loop()
    assert calls["ticks"] == 1
