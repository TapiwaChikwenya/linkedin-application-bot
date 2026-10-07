from linkedin_easy_apply.pacing import (
    delay_range,
    normalize_pace,
    should_take_long_break,
    sleep_human,
)


def test_normalize_pace_defaults_to_human():
    assert normalize_pace(None) == "human"
    assert normalize_pace("FAST") == "fast"
    assert normalize_pace("human") == "human"


def test_human_job_view_is_much_slower_than_fast():
    human_low, human_high = delay_range("human", "job_view")
    _fast_low, fast_high = delay_range("fast", "job_view")
    assert human_low >= 8
    assert human_high >= 15
    assert fast_high <= 2


def test_sleep_human_uses_injected_clock():
    sleeps = []
    seconds = sleep_human("fast", "skip", sleeper=sleeps.append, rng=lambda low, high: 0.5)
    assert seconds == 0.5
    assert sleeps == [0.5]


def test_list_scan_is_much_faster_than_job_skip():
    _scan_low, scan_high = delay_range("human", "list_scan")
    skip_low, _skip_high = delay_range("human", "skip")
    assert scan_high <= 0.5
    assert scan_high < skip_low


def test_long_break_every_seven_jobs():
    assert should_take_long_break(0) is False
    assert should_take_long_break(7) is True
    assert should_take_long_break(8) is False
