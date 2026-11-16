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
ReleaseFn = Callable[[], Any]
StatusFn = Callable[[], str]

_TERMINAL = {"completed", "stopped"}


class RunScheduler:
    """Tick local schedule windows. Empty windows mean manual Start only.

    A window that is already open starts on the first tick, not after a full
    interval. A crashed worker is started again. A completed run, or a Stop
    from the operator, stays down until the next window or a manual Start.
    """

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
        release_ollama: ReleaseFn | None = None,
        run_status: StatusFn | None = None,
    ):
        self.store = store
        self._start_run = start_run
        self._stop_run = stop_run
        self._is_alive = is_alive
        self._release_ollama = release_ollama
        self._run_status = run_status
        self.interval_sec = max(5.0, float(interval_sec))
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self._started_for = ""
        self._operator_hold = ""
        self._released_for = ""
        self._logged = ""
        self.armed = False
        self.last_action = ""
        self.last_error = ""
        self.last_reason = ""

    def _status(self) -> str:
        if self._run_status is not None:
            return str(self._run_status() or "")
        try:
            run = self.store.current_run()
        except Exception as exc:  # noqa: BLE001 - a store blip must not kill the loop
            log.warning("scheduler could not read run status: %s", exc.__class__.__name__)
            return ""
        return str((run or {}).get("status") or "")

    def note_operator_stop(self) -> None:
        """Keep this open window from immediately starting the worker again."""
        status = schedule_status(self.store, now=self._clock())
        if status.get("enabled") and status.get("active"):
            self._operator_hold = str(status.get("window_key") or "open")
            self.last_reason = "stopped by operator"
        else:
            self._operator_hold = ""

    def clear_operator_hold(self) -> None:
        self._operator_hold = ""

    def evaluate(self, now: datetime | None = None) -> dict[str, Any]:
        status = schedule_status(self.store, now=now or self._clock())
        if not status.get("enabled"):
            return {"action": "idle", "schedule": status, "reason": status.get("reason") or ""}
        if status.get("active"):
            key = str(status.get("window_key") or "")
            if self._is_alive():
                self._started_for = key
                return {"action": "idle", "schedule": status, "reason": "already running"}
            if key and self._operator_hold == key:
                return {
                    "action": "idle",
                    "schedule": status,
                    "reason": "stopped by operator",
                }
            if key and self._started_for == key and self._status() in _TERMINAL:
                return {
                    "action": "finish",
                    "schedule": status,
                    "reason": "run finished",
                }
            return {"action": "start", "schedule": status, "reason": "window open"}
        self._started_for = ""
        self._operator_hold = ""
        self._released_for = ""
        if self._is_alive():
            return {"action": "stop", "schedule": status, "reason": "window closed"}
        return {"action": "idle", "schedule": status, "reason": "outside schedule"}

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        try:
            decision = self.evaluate(now=now)
        except Exception as extra:  # noqa: BLE001 - scheduler must never kill the dashboard
            self.last_error = extra.__class__.__name__
            self.last_reason = "tick failed"
            log.warning("scheduler evaluate failed: %s", extra.__class__.__name__)
            return {"action": "error", "ok": False, "error": self.last_error, "reason": self.last_reason}
        action = str(decision.get("action") or "idle")
        reason = str(decision.get("reason") or "")
        self.last_reason = reason
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
                    log.info("scheduler started worker window=%s", self._started_for)
                else:
                    self.last_error = error or "scheduled start failed"
                    log.warning("scheduler start failed: %s", self.last_error)
                decision["ok"] = ok
                decision["error"] = self.last_error
                self._log_decision(decision)
                return decision
            if action == "stop":
                self._stop_run()
                self.last_action = "stop"
                self.last_error = ""
                decision["ok"] = True
                log.info("scheduler stopped worker: %s", reason)
                self._log_decision(decision)
                return decision
            if action == "finish":
                key = str((decision.get("schedule") or {}).get("window_key") or "")
                if key and self._released_for != key:
                    self._release()
                    self._released_for = key
                    log.info("scheduler released Ollama after run finished")
                self.last_action = "finish"
                self.last_error = ""
                decision["action"] = "idle"
                decision["ok"] = True
                self._log_decision(decision)
                return decision
            decision["ok"] = True
            self._log_decision(decision)
            return decision
        except Exception as extra:  # noqa: BLE001 - scheduler must never kill the dashboard
            self.last_error = extra.__class__.__name__
            log.warning("scheduler tick failed: %s", extra.__class__.__name__)
            decision["ok"] = False
            decision["error"] = self.last_error
            return decision

    def _release(self) -> None:
        if self._release_ollama is not None:
            self._release_ollama()
            return
        self._stop_run()

    def _log_decision(self, decision: dict[str, Any]) -> None:
        signature = "|".join(
            [
                str(decision.get("action") or ""),
                str(decision.get("reason") or ""),
                str(decision.get("error") or ""),
                str(decision.get("ok")),
            ]
        )
        if signature == self._logged:
            return
        self._logged = signature
        if decision.get("reason") == "already running":
            return
        log.info(
            "scheduler action=%s reason=%s ok=%s error=%s",
            decision.get("action") or "",
            decision.get("reason") or "",
            decision.get("ok"),
            decision.get("error") or "",
        )

    def kick(self) -> None:
        """Re-check the window soon after Settings saves it. No-op until armed."""
        if not self.armed:
            return
        threading.Thread(target=self._tick_safe, name="apply-ops-scheduler-kick", daemon=True).start()

    def _tick_safe(self, now: datetime | None = None) -> dict[str, Any]:
        if not self._tick_lock.acquire(blocking=False):
            return {"action": "idle", "reason": "tick in progress", "ok": True}
        try:
            return self.tick(now=now)
        finally:
            self._tick_lock.release()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.armed = True
            return
        self._stop.clear()
        self.armed = True
        self._thread = threading.Thread(target=self._loop, name="apply-ops-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_safe()
            except Exception as extra:  # noqa: BLE001 - keep ticking after a bad window
                self.last_error = extra.__class__.__name__
                log.warning("scheduler loop failed: %s", extra.__class__.__name__)
            if self._sleep is not None:
                self._sleep(self.interval_sec)
                continue
            if self._stop.wait(self.interval_sec):
                return
