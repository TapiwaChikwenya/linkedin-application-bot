"""Dashboard-owned auto Start/Stop from operator schedule windows."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from linkedin_easy_apply.operator_settings import schedule_status
from linkedin_easy_apply.store import Store

log = logging.getLogger(__name__)

StartFn = Callable[[], Any]
StopFn = Callable[[], Any]
AliveFn = Callable[[], bool]


class RunScheduler:
    """Tick local schedule windows. Empty windows mean manual Start only."""

    def __init__(
        self,
        store: Store,
        *,
        start_run: StartFn,
        stop_run: StopFn,
        is_alive: AliveFn,
        interval_sec: float = 20.0,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.store = store
        self._start_run = start_run
        self._stop_run = stop_run
        self._is_alive = is_alive
        self.interval_sec = max(5.0, float(interval_sec))
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_for = ""
        self.last_action = ""
        self.last_error = ""

    def evaluate(self, now: datetime | None = None) -> dict[str, Any]:
        status = schedule_status(self.store, now=now or self._clock())
        if not status.get("enabled"):
            return {"action": "idle", "schedule": status, "reason": status.get("reason") or ""}
        if status.get("active"):
            key = str(status.get("window_key") or "")
            if self._is_alive():
                self._started_for = key
                return {"action": "idle", "schedule": status, "reason": "already running"}
            if key and self._started_for == key:
                return {
                    "action": "idle",
                    "schedule": status,
                    "reason": "already started this window",
                }
            return {"action": "start", "schedule": status, "reason": "window open"}
        self._started_for = ""
        if self._is_alive():
            return {"action": "stop", "schedule": status, "reason": "window closed"}
        return {"action": "idle", "schedule": status, "reason": "outside schedule"}

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        decision = self.evaluate(now=now)
        action = str(decision.get("action") or "idle")
        try:
            if action == "start":
                result = self._start_run()
                ok = True
                error = ""
                if isinstance(result, dict):
                    ok = bool(result.get("ok", True))
                    error = str(result.get("error") or "")
                if ok:
                    self._started_for = str((decision.get("schedule") or {}).get("window_key") or "")
                    self.last_action = "start"
                    self.last_error = ""
                else:
                    self.last_error = error or "scheduled start failed"
                decision["ok"] = ok
                decision["error"] = self.last_error
                return decision
            if action == "stop":
                self._stop_run()
                self.last_action = "stop"
                self.last_error = ""
                decision["ok"] = True
                return decision
            decision["ok"] = True
            return decision
        except Exception as extra:  # noqa: BLE001 - scheduler must never kill the dashboard
            self.last_error = extra.__class__.__name__
            log.warning("scheduler tick failed: %s", extra.__class__.__name__)
            decision["ok"] = False
            decision["error"] = self.last_error
            return decision

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="apply-ops-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self.tick()
