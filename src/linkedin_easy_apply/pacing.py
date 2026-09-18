"""Human-like delays used by the Easy Apply workflow."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

HUMAN_DELAYS: dict[str, tuple[float, float]] = {
    "search_page": (6.0, 12.0),
    "job_view": (10.0, 22.0),
    "skip": (3.5, 8.0),
    "apply_step": (2.5, 6.0),
    "after_apply": (18.0, 40.0),
    "field": (0.8, 2.0),
    "type_key": (0.08, 0.22),
    "long_break": (25.0, 55.0),
}

FAST_DELAYS: dict[str, tuple[float, float]] = {
    "search_page": (1.0, 2.0),
    "job_view": (1.0, 2.0),
    "skip": (0.4, 0.8),
    "apply_step": (0.25, 0.5),
    "after_apply": (1.0, 2.0),
    "field": (0.1, 0.2),
    "type_key": (0.0, 0.02),
    "long_break": (1.0, 2.0),
}


def normalize_pace(pace: str | None) -> str:
    value = (pace or "human").strip().lower()
    return "fast" if value == "fast" else "human"


def delay_range(pace: str | None, action: str) -> tuple[float, float]:
    delays = FAST_DELAYS if normalize_pace(pace) == "fast" else HUMAN_DELAYS
    if action not in delays:
        raise KeyError("unknown pacing action: " + action)
    return delays[action]


def sleep_human(
    pace: str | None,
    action: str,
    sleeper: Callable[[float], None] = time.sleep,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    low, high = delay_range(pace, action)
    seconds = rng(low, high)
    sleeper(seconds)
    return seconds


def should_take_long_break(jobs_seen: int, every: int = 7) -> bool:
    return jobs_seen > 0 and every > 0 and jobs_seen % every == 0
