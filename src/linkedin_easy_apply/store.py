"""SQLite history for jobs, runs, and operator events."""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from linkedin_easy_apply.question_capture import (
    normalize_question,
    option_set_hash,
    redact_proposed_value,
)

log = logging.getLogger(__name__)

APPROVED_RETRIEVAL_POOL = 500
APPROVED_PROMPT_LIMIT = 12
SENSITIVE_QUESTION_TOKENS = frozenset(
    {
        "authorization",
        "authorized",
        "authorised",
        "clearance",
        "compensation",
        "disability",
        "disabled",
        "ethnic",
        "ethnicity",
        "gender",
        "race",
        "salary",
        "sponsor",
        "sponsored",
        "sponsorship",
        "veteran",
        "veterans",
    }
)
_QUESTION_STOPWORDS = frozenset(
    {
        "a", "about", "after", "an", "and", "any", "are", "as", "at", "be",
        "been", "before", "being", "but", "by", "can", "could", "did", "do",
        "does", "for", "from", "had", "has", "have", "how", "if", "in", "into",
        "is", "it", "its", "many", "me", "much", "my", "not", "of", "on", "or",
        "our", "over", "please", "should", "so", "than", "that", "the", "their",
        "them", "then", "these", "they", "this", "those", "to", "we", "were",
        "what", "when", "which", "who", "will", "with", "would", "you", "your",
    }
)

DEFAULT_STORE_PATH = os.path.join("data", "assistant.sqlite")
SQLITE_TIMEOUT_SEC = 2.5
SQLITE_BUSY_TIMEOUT_MS = 2500
BACKFILL_CHUNK = 40
_F = TypeVar("_F", bound=Callable[..., Any])

STATUSES = (
    "seen",
    "skipped_filter",
    "skipped_fit",
    "needs_review",
    "applied",
    "failed",
    "already_applied",
    "page_timeout",
)
JOB_STATUS_GROUPS = {
    "applied": ("applied",),
    "skipped": ("skipped_filter", "skipped_fit", "needs_review", "already_applied", "page_timeout"),
    "failed": ("failed",),
}
QUESTION_ORIGINS = ("linkedin", "seed", "llm")
QUESTION_SOURCES = ("mapped", "llm", "unanswered", "manual", "seed")
QUESTION_STATUSES = ("pending", "approved", "rejected")
QUESTION_COLUMNS = (
    "id",
    "normalized_question",
    "raw_question",
    "field_kind",
    "options_json",
    "option_set_hash",
    "required",
    "job_id",
    "company",
    "title",
    "source",
    "proposed_value",
    "approved_value",
    "approval_status",
    "provenance",
    "confidence",
    "seen_count",
    "reuse_count",
    "last_seen_at",
    "created_at",
    "cleaned_question",
    "classified_kind",
    "answer_shape",
    "cluster_key",
    "merged_into_id",
    "classification_status",
    "not_user_answerable",
)

VISIBLE_QUESTION_SQL = (
    "(merged_into_id IS NULL OR merged_into_id = 0)"
    " AND IFNULL(not_user_answerable, 0) = 0"
    " AND LOWER(IFNULL(classified_kind, '')) != 'file'"
    " AND LOWER(IFNULL(classification_status, '')) != 'duplicate'"
)

SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    status TEXT NOT NULL,
    applied_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    location_text TEXT,
    sector TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    workplace TEXT,
    url TEXT,
    fit_score REAL,
    status TEXT NOT NULL,
    reason TEXT,
    screenshot_path TEXT,
    html_path TEXT,
    seen_at TEXT NOT NULL,
    applied_at TEXT,
    last_run_id TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    job_id TEXT,
    message TEXT NOT NULL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id);
"""
SCHEMA = SCHEMA_TABLES
# Additive jobs indexes are created in _ensure_jobs_schema after ALTER.
# Never put idx_jobs_seen_outcome in SCHEMA: CREATE TABLE IF NOT EXISTS does not
# add `outcome` to an existing jobs table, and executescript would abort init.
JOBS_INDEXES = (
    (None, "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"),
    (None, "CREATE INDEX IF NOT EXISTS idx_jobs_seen_at ON jobs(seen_at)"),
    ("outcome", "CREATE INDEX IF NOT EXISTS idx_jobs_seen_outcome ON jobs(seen_at, outcome)"),
    ("sector", "CREATE INDEX IF NOT EXISTS idx_jobs_sector ON jobs(sector)"),
    ("market", "CREATE INDEX IF NOT EXISTS idx_jobs_market ON jobs(market)"),
)

# Capture schema. initialize() CREATEs if missing and ALTERs in new columns.
# Do not DROP/RENAME this table; identity is (normalized_question, field_kind, option_set_hash).
QUESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_question TEXT NOT NULL,
    raw_question TEXT NOT NULL,
    field_kind TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    option_set_hash TEXT NOT NULL DEFAULT '',
    required INTEGER NOT NULL DEFAULT 0,
    job_id TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'unanswered',
    proposed_value TEXT NOT NULL DEFAULT '',
    approved_value TEXT NOT NULL DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'pending',
    provenance TEXT NOT NULL DEFAULT '',
    confidence REAL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    reuse_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleaned_question TEXT NOT NULL DEFAULT '',
    classified_kind TEXT NOT NULL DEFAULT '',
    answer_shape TEXT NOT NULL DEFAULT '',
    cluster_key TEXT NOT NULL DEFAULT '',
    merged_into_id INTEGER,
    classification_status TEXT NOT NULL DEFAULT '',
    not_user_answerable INTEGER NOT NULL DEFAULT 0,
    CHECK (source IN ('mapped', 'llm', 'unanswered', 'manual', 'seed')),
    CHECK (approval_status IN ('pending', 'approved', 'rejected'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_identity
    ON questions(normalized_question, field_kind, option_set_hash);
CREATE INDEX IF NOT EXISTS idx_questions_approval
    ON questions(approval_status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_questions_cluster
    ON questions(cluster_key);
"""

OPERATOR_DDL = """
CREATE TABLE IF NOT EXISTS operator_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(id);
"""
CHAT_HISTORY_LIMIT = 80


def default_store_path() -> str:
    return os.getenv("LINKEDIN_STORE_PATH", DEFAULT_STORE_PATH)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_question_key(text: str) -> str:
    return normalize_question(text)


def question_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", normalize_question(text))
    return {token for token in cleaned.split() if token and token not in _QUESTION_STOPWORDS}


def is_sensitive_question(text: str) -> bool:
    tokens = question_tokens(text)
    if tokens & SENSITIVE_QUESTION_TOKENS:
        return True
    lowered = normalize_question(text)
    return any(hint in lowered for hint in ("clearance", "sponsorship", "disability"))


def compact_approved_row(
    row: dict[str, Any],
    match: str = "exact",
    note: str = "",
) -> dict[str, str]:
    question = str(
        row.get("raw_question") or row.get("question") or row.get("question_text") or ""
    ).strip()
    if not question:
        question = str(row.get("normalized_question") or "").strip()
    value = str(row.get("approved_value") or row.get("value") or "").strip()
    ident = row.get("id")
    if ident in (None, ""):
        ident = row.get("question_id") or row.get("source_id")
    payload = {
        "source_id": str(ident or ""),
        "question": question,
        "value": value,
        "match": match,
    }
    extra_note = str(note or row.get("note") or "").strip()
    if extra_note:
        payload["note"] = extra_note
    return payload


def conservative_token_overlap(query: str, stored: str) -> bool:
    """High-precision overlap so 'SQL years' does not match 'Python years'."""
    left = question_tokens(query)
    right = question_tokens(stored)
    if not left or not right:
        return False
    if left == right:
        return True
    overlap = left & right
    if len(overlap) < 3:
        return False
    shorter = left if len(left) <= len(right) else right
    if len(overlap) / len(shorter) < 0.8:
        return False
    return len(overlap) / len(left | right) >= 0.55


def match_approved_answers(
    approved: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    limit: int = APPROVED_PROMPT_LIMIT,
) -> list[dict[str, str]]:
    """Exact match, conservative overlap, then related-skill / same-family interpolation."""
    from linkedin_easy_apply.inference import interpolate_approved_for_question

    pool = [
        row
        for row in approved
        if str(row.get("approved_value") or row.get("value") or "").strip()
    ]
    used: set[str] = set()
    matched: list[dict[str, str]] = []

    def take(row: dict[str, Any], match: str, note: str = "") -> None:
        payload = compact_approved_row(row, match=match, note=note)
        if not (payload["source_id"] and payload["question"] and payload["value"]):
            return
        used.add(payload["source_id"])
        matched.append(payload)

    for question in questions:
        if len(matched) >= limit:
            break
        needle = normalize_question(str(question.get("question") or ""))
        if not needle:
            continue
        exact = None
        for row in pool:
            ident = str(row.get("id") or row.get("question_id") or row.get("source_id") or "")
            if ident in used:
                continue
            stored = normalize_question(
                str(
                    row.get("normalized_question")
                    or row.get("raw_question")
                    or row.get("question")
                    or ""
                )
            )
            if stored == needle:
                exact = row
                break
        if exact is not None:
            take(exact, "exact")
            continue
        best: dict[str, Any] | None = None
        best_score = 0.0
        query_tokens = question_tokens(needle)
        for row in pool:
            ident = str(row.get("id") or row.get("question_id") or row.get("source_id") or "")
            if ident in used:
                continue
            stored_text = str(
                row.get("normalized_question")
                or row.get("raw_question")
                or row.get("question")
                or ""
            )
            if not conservative_token_overlap(needle, stored_text):
                continue
            stored_tokens = question_tokens(stored_text)
            union = query_tokens | stored_tokens
            score = len(query_tokens & stored_tokens) / max(len(union), 1)
            if score > best_score:
                best = row
                best_score = score
        if best is not None:
            take(best, "overlap")
            continue
        interpolated = interpolate_approved_for_question(pool, question, used)
        if interpolated is None:
            continue
        source_id = str(interpolated.get("source_id") or "")
        if source_id:
            used.add(source_id)
        matched.append(interpolated)
    return matched[:limit]


def extract_job_id(url: str) -> str:
    marker = "/jobs/view/"
    if marker not in (url or ""):
        return ""
    tail = url.split(marker, 1)[1]
    digits = []
    for char in tail:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return "".join(digits)


def question_origin(source: str, provenance: str = "") -> str:
    src = str(source or "").strip().lower()
    prov = str(provenance or "").strip().lower()
    if src == "llm":
        return "llm"
    if src == "seed" or prov == "seed":
        return "seed"
    return "linkedin"


def job_decision(status: str) -> str:
    value = str(status or "").strip().lower()
    if value == "applied":
        return "apply"
    if value == "failed":
        return "fail"
    if value in JOB_STATUS_GROUPS["skipped"]:
        return "skip"
    return value or "seen"


def grouped_job_counts(counts: dict[str, int]) -> dict[str, int]:
    applied = int(counts.get("applied") or 0)
    skipped = sum(int(counts.get(status) or 0) for status in JOB_STATUS_GROUPS["skipped"])
    failed = int(counts.get("failed") or 0)
    return {
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "seen": int(counts.get("seen") or 0),
        "total": int(counts.get("total") or 0),
    }


def status_from_result(result: str) -> str:
    text = (result or "").lower()
    if "just applied" in text:
        return "applied"
    if "did not finish loading" in text:
        return "page_timeout"
    if "skipped before open" in text:
        if "already applied" in text:
            return "already_applied"
        if "no easy apply" in text or "off-site" in text:
            return "failed"
        return "skipped_filter"
    if (
        "skipped by title" in text
        or "company filter" in text
        or "search card title missing" in text
    ):
        return "skipped_filter"
    if "job fit llama=" in text and " skip" in text:
        return "skipped_fit"
    if "unanswered required" in text:
        return "needs_review"
    if "already applied" in text or "easy apply unavailable" in text:
        return "already_applied"
    if "couldn't apply" in text or "couldnt apply" in text:
        return "failed"
    if (
        "off-site apply" in text
        or "easy apply control not found" in text
        or "no easy apply on search card" in text
    ):
        return "failed"
    return "seen"


def _store_tx(write: bool) -> Callable[[_F], _F]:
    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapped(self: Store, *args: Any, **kwargs: Any) -> Any:
            with self.connection(write=write):
                return fn(self, *args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator


class Store:
    def __init__(self, path: str = DEFAULT_STORE_PATH):
        self.path = path
        self._tls = threading.local()
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=SQLITE_TIMEOUT_SEC,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS:d}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        existing = getattr(self._tls, "conn", None)
        if existing is not None:
            yield existing
            return
        conn = self._connect()
        self._tls.conn = conn
        started = False
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
                started = True
            yield conn
            if started and conn.in_transaction:
                conn.execute("COMMIT")
        except Exception:
            if started:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            self._tls.conn = None
            conn.close()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            raise sqlite3.ProgrammingError("SQLite connection is not open for this operation")
        return conn

    def initialize(self) -> None:
        with self.connection(write=False) as conn:
            try:
                conn.executescript(SCHEMA_TABLES)
            except sqlite3.OperationalError as exc:
                if "no such column" not in str(exc).lower():
                    raise
            self._ensure_jobs_schema()
            self._ensure_questions_schema()
            self._ensure_operator_schema()
        self.backfill_job_metrics()

    def close(self) -> None:
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            conn.close()
            self._tls.conn = None

    def start_run(self, run_id: str | None = None) -> str:
        ident = run_id or uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO runs(run_id, started_at, status) VALUES (?, ?, ?)",
            (ident, utc_now(), "running"),
        )
        self._conn.commit()
        self.add_event(ident, "info", None, "Run started")
        return ident

    def finish_run(self, run_id: str, status: str) -> None:
        applied = self.count_jobs(run_id, "applied")
        skipped = self.count_jobs(
            run_id,
            "skipped_filter",
            "skipped_fit",
            "needs_review",
            "already_applied",
            "page_timeout",
        )
        self._conn.execute(
            """
            UPDATE runs
            SET stopped_at = ?, status = ?, applied_count = ?, skipped_count = ?
            WHERE run_id = ?
            """,
            (utc_now(), status, applied, skipped, run_id),
        )
        self._conn.commit()
        self.add_event(run_id, "info", None, "Run " + status)

    def count_jobs(self, run_id: str, *statuses: str) -> int:
        if not statuses:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE last_run_id = ?",
                (run_id,),
            ).fetchone()
            return int(row["n"] if row else 0)
        placeholders = ",".join("?" for _ in statuses)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE last_run_id = ? AND status IN ({placeholders})",
            (run_id, *statuses),
        ).fetchone()
        return int(row["n"] if row else 0)

    def current_run(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def running_run(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def restore_running_run(self, run_id: str) -> None:
        """Undo a false crash classification while its owned worker lease is live."""
        self._conn.execute(
            """
            UPDATE runs
            SET stopped_at = NULL, status = 'running'
            WHERE run_id = ? AND status = 'crashed'
            """,
            (run_id,),
        )
        self._conn.commit()

    def upsert_job(
        self,
        job_id: str,
        *,
        title: str = "",
        company: str = "",
        location: str = "",
        location_text: str = "",
        sector: str = "",
        market: str = "",
        outcome: str = "",
        workplace: str = "",
        url: str = "",
        status: str = "seen",
        reason: str = "",
        run_id: str | None = None,
        screenshot_path: str = "",
        html_path: str = "",
        fit_score: float | None = None,
        metrics_json: str = "",
    ) -> None:
        if not job_id:
            return
        existing = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        now = utc_now()
        applied_at = now if status == "applied" else (existing["applied_at"] if existing else None)
        metrics = self._job_metric_values(
            existing,
            title=title,
            company=company,
            location=location,
            location_text=location_text,
            sector=sector,
            market=market,
            outcome=outcome,
            status=status,
            job_id=job_id,
            metrics_json=metrics_json,
        )
        if existing:
            self._conn.execute(
                """
                UPDATE jobs SET
                    title = COALESCE(NULLIF(?, ''), title),
                    company = COALESCE(NULLIF(?, ''), company),
                    location = COALESCE(NULLIF(?, ''), location),
                    location_text = COALESCE(NULLIF(?, ''), location_text),
                    sector = COALESCE(NULLIF(?, ''), sector),
                    market = COALESCE(NULLIF(?, ''), market),
                    outcome = COALESCE(NULLIF(?, ''), outcome),
                    workplace = COALESCE(NULLIF(?, ''), workplace),
                    url = COALESCE(NULLIF(?, ''), url),
                    status = ?,
                    reason = ?,
                    screenshot_path = COALESCE(NULLIF(?, ''), screenshot_path),
                    html_path = COALESCE(NULLIF(?, ''), html_path),
                    fit_score = COALESCE(?, fit_score),
                    applied_at = ?,
                    last_run_id = COALESCE(?, last_run_id),
                    metrics_json = COALESCE(NULLIF(?, ''), metrics_json)
                WHERE job_id = ?
                """,
                (
                    title,
                    company,
                    metrics["location"],
                    metrics["location_text"],
                    metrics["sector"],
                    metrics["market"],
                    metrics["outcome"],
                    workplace,
                    url,
                    status,
                    reason,
                    screenshot_path,
                    html_path,
                    fit_score,
                    applied_at,
                    run_id,
                    metrics["metrics_json"],
                    job_id,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO jobs(
                    job_id, title, company, location, location_text, sector, market,
                    outcome, workplace, url, fit_score, status, reason,
                    screenshot_path, html_path, seen_at, applied_at, last_run_id,
                    metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    title,
                    company,
                    metrics["location"],
                    metrics["location_text"],
                    metrics["sector"],
                    metrics["market"],
                    metrics["outcome"],
                    workplace,
                    url,
                    fit_score,
                    status,
                    reason,
                    screenshot_path,
                    html_path,
                    now,
                    applied_at,
                    run_id,
                    metrics["metrics_json"],
                ),
            )
        self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return one jobs row, or None when the id has not been recorded."""
        if not job_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def add_event(
        self,
        run_id: str | None,
        level: str,
        job_id: str | None,
        message: str,
        payload_json: str | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO events(run_id, created_at, level, job_id, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, utc_now(), level, job_id, message, payload_json),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_jobs(
        self,
        status: str = "",
        query: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        grouped = JOB_STATUS_GROUPS.get(str(status or "").strip().lower())
        if grouped:
            placeholders = ",".join("?" for _ in grouped)
            clauses.append("status IN (" + placeholders + ")")
            params.extend(grouped)
        elif status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            like = "%" + query + "%"
            clauses.append(
                "(title LIKE ? OR company LIKE ? OR location LIKE ? OR location_text LIKE ?"
                " OR sector LIKE ? OR market LIKE ? OR job_id LIKE ? OR reason LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            "SELECT * FROM jobs" + where + " ORDER BY seen_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, run_id: str | None = None, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        clauses = ["event_id > ?"]
        params: list[Any] = [after_id]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        rows = self._conn.execute(
            "SELECT * FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY event_id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(row) for row in rows]

    def job_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        counts = {status: 0 for status in STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        counts["total"] = sum(int(row["n"]) for row in rows)
        return counts

    def latest_job_for_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE last_run_id = ?
            ORDER BY seen_at DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def latest_job(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs ORDER BY seen_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def latest_failure(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('failed', 'page_timeout')
            ORDER BY seen_at DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def _table_columns(self, table: str) -> list[str]:
        rows = self._conn.execute("PRAGMA table_info(" + table + ")").fetchall()
        return [str(row["name"]) for row in rows]

    def _ensure_jobs_schema(self) -> None:
        """ALTER additive columns on existing jobs tables, then create indexes.

        New databases get `outcome` from SCHEMA_TABLES. Legacy tables created
        before metrics still lack that column; CREATE TABLE IF NOT EXISTS will
        not add it. Indexes that mention `outcome` must wait until ALTER lands.
        """
        cols = self._table_columns("jobs")
        if not cols:
            self._conn.executescript(SCHEMA_TABLES)
            cols = self._table_columns("jobs")
        if not cols:
            return
        additions = {
            "location_text": "TEXT",
            "sector": "TEXT NOT NULL DEFAULT ''",
            "market": "TEXT NOT NULL DEFAULT ''",
            "outcome": "TEXT NOT NULL DEFAULT ''",
            "metrics_json": "TEXT",
        }
        present = set(cols)
        for name, definition in additions.items():
            if name not in present:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN " + name + " " + definition)
        present = set(self._table_columns("jobs"))
        for required, sql in JOBS_INDEXES:
            if required and required not in present:
                continue
            self._conn.execute(sql)

    def _existing_job_value(self, existing: sqlite3.Row | None, key: str) -> str:
        if existing is None:
            return ""
        try:
            value = existing[key]
        except (IndexError, KeyError):
            return ""
        return str(value or "")

    def _job_metric_values(
        self,
        existing: sqlite3.Row | None,
        *,
        title: str,
        company: str,
        location: str,
        location_text: str,
        sector: str,
        market: str,
        outcome: str,
        status: str,
        job_id: str,
        metrics_json: str,
    ) -> dict[str, str]:
        from linkedin_easy_apply.job_metrics import (
            classify_taxonomy,
            metrics_payload_json,
            outcome_from_status,
        )

        resolved_location = str(location or location_text or "").strip()
        if not resolved_location:
            resolved_location = self._existing_job_value(existing, "location")
        resolved_location_text = str(location_text or location or "").strip()
        if not resolved_location_text:
            resolved_location_text = self._existing_job_value(existing, "location_text")
        if not resolved_location_text:
            resolved_location_text = resolved_location
        if not resolved_location:
            resolved_location = resolved_location_text
        resolved_outcome = str(outcome or "").strip() or outcome_from_status(status)
        resolved_sector = str(sector or "").strip()
        resolved_market = str(market or "").strip()
        if not resolved_sector:
            resolved_sector = self._existing_job_value(existing, "sector")
        if not resolved_market:
            resolved_market = self._existing_job_value(existing, "market")
        resolved_title = str(title or "").strip() or self._existing_job_value(existing, "title")
        resolved_company = str(company or "").strip() or self._existing_job_value(
            existing, "company"
        )
        if (not resolved_sector or not resolved_market) and (resolved_title or resolved_company):
            classified = classify_taxonomy(
                resolved_title,
                resolved_company,
                "",
                resolved_location_text,
            )
            resolved_sector = resolved_sector or classified.sector
            resolved_market = resolved_market or classified.market
        payload = str(metrics_json or "").strip()
        if not payload:
            payload = self._existing_job_value(existing, "metrics_json")
        if not payload:
            payload = metrics_payload_json(
                {
                    "location_text": resolved_location_text,
                    "sector": resolved_sector,
                    "market": resolved_market,
                    "job_id": job_id,
                    "title": resolved_title,
                    "company": resolved_company,
                    "outcome": resolved_outcome,
                    "source": "taxonomy",
                }
            )
        return {
            "location": resolved_location,
            "location_text": resolved_location_text,
            "sector": resolved_sector,
            "market": resolved_market,
            "outcome": resolved_outcome,
            "metrics_json": payload,
        }

    def _apply_metric_chunk(self, rows: list[tuple[dict[str, str], str]]) -> None:
        with self.connection(write=True) as conn:
            for metrics, job_id in rows:
                conn.execute(
                    """
                    UPDATE jobs
                    SET location = COALESCE(NULLIF(?, ''), location),
                        location_text = ?,
                        sector = ?,
                        market = ?,
                        outcome = ?,
                        metrics_json = COALESCE(NULLIF(?, ''), metrics_json)
                    WHERE job_id = ?
                    """,
                    (
                        metrics["location"],
                        metrics["location_text"],
                        metrics["sector"],
                        metrics["market"],
                        metrics["outcome"],
                        metrics["metrics_json"],
                        job_id,
                    ),
                )

    def backfill_job_metrics(self, limit: int = 2000) -> int:
        """Fill sector/market/outcome/location_text from title+company on recent rows."""
        with self.connection(write=False):
            cols = set(self._table_columns("jobs"))
            if "sector" not in cols:
                return 0
            fetched = self._conn.execute(
                """
                SELECT job_id, title, company, location, location_text, status, sector,
                       market, outcome, metrics_json
                FROM jobs
                WHERE (TRIM(COALESCE(title, '')) != '' OR TRIM(COALESCE(company, '')) != '')
                  AND (
                    TRIM(COALESCE(sector, '')) = ''
                    OR TRIM(COALESCE(market, '')) = ''
                    OR TRIM(COALESCE(outcome, '')) = ''
                    OR TRIM(COALESCE(location_text, '')) = ''
                  )
                ORDER BY seen_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            rows = [dict(row) for row in fetched]
        updated = 0
        pending: list[tuple[dict[str, str], str]] = []
        for row in rows:
            metrics = self._job_metric_values(
                row,
                title=str(row["title"] or ""),
                company=str(row["company"] or ""),
                location=str(row["location"] or ""),
                location_text=str(row["location_text"] or ""),
                sector=str(row["sector"] or ""),
                market=str(row["market"] or ""),
                outcome=str(row["outcome"] or ""),
                status=str(row["status"] or ""),
                job_id=str(row["job_id"] or ""),
                metrics_json=str(row["metrics_json"] or ""),
            )
            pending.append((metrics, str(row["job_id"])))
            if len(pending) >= BACKFILL_CHUNK:
                self._apply_metric_chunk(pending)
                updated += len(pending)
                pending = []
        if pending:
            self._apply_metric_chunk(pending)
            updated += len(pending)
        return updated

    def job_metrics(self, now: datetime | None = None) -> dict[str, Any]:
        """Aggregate operator metrics for today and the last 7 days via SQL GROUP BY."""
        from linkedin_easy_apply.job_metrics import (
            OUTCOMES,
            empty_metrics_window,
            taxonomy_catalog,
        )

        moment = now or datetime.now(timezone.utc)
        until = moment.isoformat(timespec="seconds")
        today_start = moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
            timespec="seconds"
        )
        week_start = (moment - timedelta(days=7)).isoformat(timespec="seconds")

        def window_payload(since: str) -> dict[str, Any]:
            payload = empty_metrics_window(since, until)
            payload["totals"] = {key: 0 for key in (*OUTCOMES, "total")}
            return payload

        today = window_payload(today_start)
        week = window_payload(week_start)
        if "sector" not in set(self._table_columns("jobs")):
            return {
                "source": "store",
                "today": today,
                "last_7_days": week,
                "taxonomy": taxonomy_catalog(),
            }

        dim_queries = {
            "by_sector": (
                "COALESCE(NULLIF(TRIM(sector), ''), 'other')",
                "other",
            ),
            "by_market": (
                "COALESCE(NULLIF(TRIM(market), ''), 'other')",
                "other",
            ),
            "by_location": (
                (
                    "COALESCE(NULLIF(TRIM(location_text), ''),"
                    " NULLIF(TRIM(location), ''), '(unknown)')"
                ),
                "(unknown)",
            ),
        }
        for field, (expr, _default) in dim_queries.items():
            rows = self._conn.execute(
                f"""
                SELECT {expr} AS key,
                       COALESCE(NULLIF(TRIM(outcome), ''), 'seen') AS outcome,
                       SUM(CASE WHEN seen_at >= ? THEN 1 ELSE 0 END) AS today_n,
                       COUNT(*) AS week_n
                FROM jobs
                WHERE seen_at >= ?
                GROUP BY 1, 2
                """,
                (today_start, week_start),
            ).fetchall()
            today[field] = self._pivot_metric_rows(rows, "today_n")
            week[field] = self._pivot_metric_rows(rows, "week_n")

        outcome_rows = self._conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(outcome), ''), 'seen') AS key,
                   SUM(CASE WHEN seen_at >= ? THEN 1 ELSE 0 END) AS today_n,
                   COUNT(*) AS week_n
            FROM jobs
            WHERE seen_at >= ?
            GROUP BY 1
            """,
            (today_start, week_start),
        ).fetchall()
        today["by_outcome"] = self._outcome_rows(outcome_rows, "today_n")
        week["by_outcome"] = self._outcome_rows(outcome_rows, "week_n")
        today["totals"] = self._totals_from_outcomes(today["by_outcome"])
        week["totals"] = self._totals_from_outcomes(week["by_outcome"])
        return {
            "source": "store",
            "today": today,
            "last_7_days": week,
            "taxonomy": taxonomy_catalog(),
        }

    def _pivot_metric_rows(self, rows: list[sqlite3.Row], count_key: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row["key"] or "other")
            outcome = str(row["outcome"] or "seen")
            count = int(row[count_key] or 0)
            if count <= 0:
                continue
            bucket = grouped.setdefault(
                key,
                {"key": key, "applied": 0, "skipped": 0, "failed": 0, "seen": 0, "count": 0},
            )
            if outcome in {"applied", "skipped", "failed", "seen"}:
                bucket[outcome] += count
            bucket["count"] += count
        return sorted(grouped.values(), key=lambda item: (-int(item["count"]), str(item["key"])))

    def _outcome_rows(self, rows: list[sqlite3.Row], count_key: str) -> list[dict[str, Any]]:
        from linkedin_easy_apply.job_metrics import OUTCOMES

        counts = {key: 0 for key in OUTCOMES}
        for row in rows:
            key = str(row["key"] or "seen")
            count = int(row[count_key] or 0)
            if key in counts:
                counts[key] += count
            elif count:
                counts["seen"] += count
        return [{"key": key, "count": counts[key]} for key in OUTCOMES if counts[key]]

    def _totals_from_outcomes(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        from linkedin_easy_apply.job_metrics import OUTCOMES

        totals = {key: 0 for key in (*OUTCOMES, "total")}
        for row in rows:
            key = str(row.get("key") or "")
            count = int(row.get("count") or 0)
            if key in OUTCOMES:
                totals[key] = count
            totals["total"] += count
        return totals

    def _ensure_questions_schema(self) -> None:
        """Create the landed capture table or ALTER in missing columns. Never rebuild."""
        cols = self._table_columns("questions")
        if not cols:
            self._conn.executescript(QUESTIONS_DDL)
            return
        self._add_missing_question_columns(cols)
        self._conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_identity
                ON questions(normalized_question, field_kind, option_set_hash);
            CREATE INDEX IF NOT EXISTS idx_questions_approval
                ON questions(approval_status, last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_questions_cluster
                ON questions(cluster_key);
            """
        )

    def _add_missing_question_columns(self, cols: list[str]) -> None:
        additions = {
            "raw_question": "TEXT NOT NULL DEFAULT ''",
            "field_kind": "TEXT NOT NULL DEFAULT ''",
            "options_json": "TEXT NOT NULL DEFAULT '[]'",
            "option_set_hash": "TEXT NOT NULL DEFAULT ''",
            "required": "INTEGER NOT NULL DEFAULT 0",
            "job_id": "TEXT NOT NULL DEFAULT ''",
            "company": "TEXT NOT NULL DEFAULT ''",
            "title": "TEXT NOT NULL DEFAULT ''",
            "source": "TEXT NOT NULL DEFAULT 'unanswered'",
            "proposed_value": "TEXT NOT NULL DEFAULT ''",
            "approved_value": "TEXT NOT NULL DEFAULT ''",
            "approval_status": "TEXT NOT NULL DEFAULT 'pending'",
            "provenance": "TEXT NOT NULL DEFAULT ''",
            "confidence": "REAL",
            "seen_count": "INTEGER NOT NULL DEFAULT 1",
            "reuse_count": "INTEGER NOT NULL DEFAULT 0",
            "last_seen_at": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "cleaned_question": "TEXT NOT NULL DEFAULT ''",
            "classified_kind": "TEXT NOT NULL DEFAULT ''",
            "answer_shape": "TEXT NOT NULL DEFAULT ''",
            "cluster_key": "TEXT NOT NULL DEFAULT ''",
            "merged_into_id": "INTEGER",
            "classification_status": "TEXT NOT NULL DEFAULT ''",
            "not_user_answerable": "INTEGER NOT NULL DEFAULT 0",
        }
        present = set(cols)
        for name, definition in additions.items():
            if name not in present:
                self._conn.execute("ALTER TABLE questions ADD COLUMN " + name + " " + definition)

    def _question_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["required"] = bool(item.get("required"))
        item["seen_count"] = int(item.get("seen_count") or 0)
        item["reuse_count"] = int(item.get("reuse_count") or 0)
        confidence = item.get("confidence")
        item["confidence"] = float(confidence) if confidence is not None else None
        options_raw = item.get("options_json") or "[]"
        try:
            parsed = json.loads(options_raw) if isinstance(options_raw, str) else options_raw
            item["options"] = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            item["options"] = []
        item["question_id"] = str(item.get("id") or "")
        item["source_id"] = str(item.get("id") or "")
        item["status"] = str(item.get("approval_status") or "pending")
        cleaned = str(item.get("cleaned_question") or "").strip()
        raw = str(item.get("raw_question") or "")
        item["cleaned_question"] = cleaned
        classified = str(item.get("classified_kind") or "").strip()
        item["kind"] = classified or str(item.get("field_kind") or "")
        item["question_text"] = cleaned or raw
        item["answer_shape"] = str(item.get("answer_shape") or "")
        merged = item.get("merged_into_id")
        item["merged_into_id"] = int(merged) if merged not in (None, "") else None
        item["not_user_answerable"] = bool(item.get("not_user_answerable"))
        item["last_job_id"] = str(item.get("job_id") or "")
        item["last_job_title"] = str(item.get("title") or "")
        item["last_job_company"] = str(item.get("company") or "")
        item["origin"] = question_origin(
            str(item.get("source") or ""),
            str(item.get("provenance") or ""),
        )
        item["updated_at"] = str(item.get("last_seen_at") or "")
        return item

    def seed_source_name(self) -> str:
        """Use source=seed when the table CHECK allows it; otherwise source=manual."""
        cached = getattr(self, "_seed_source_name", None)
        if cached:
            return str(cached)
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='questions'"
        ).fetchone()
        sql = str(row[0] if row else "").lower()
        if "seed" in sql or "check (source in" not in sql:
            self._seed_source_name = "seed"
        else:
            self._seed_source_name = "manual"
        return str(self._seed_source_name)

    def question_identity_exists(
        self,
        raw_question: str,
        field_kind: str = "text",
        options: list[str] | None = None,
    ) -> bool:
        raw = " ".join(str(raw_question or "").split())
        normalized = normalize_question(raw)
        if not normalized:
            return False
        kind = str(field_kind or "text").lower() or "text"
        option_list = [str(option) for option in (options or []) if str(option).strip()]
        hashed = option_set_hash(kind, option_list)
        row = self._conn.execute(
            """
            SELECT 1 FROM questions
            WHERE normalized_question = ? AND field_kind = ? AND option_set_hash = ?
            LIMIT 1
            """,
            (normalized, kind, hashed),
        ).fetchone()
        return row is not None

    def upsert_question(
        self,
        *,
        raw_question: str,
        field_kind: str = "text",
        options: list[str] | None = None,
        required: bool = False,
        job_id: str = "",
        company: str = "",
        title: str = "",
        source: str = "unanswered",
        proposed_value: str = "",
        provenance: str = "",
        confidence: float | None = None,
        increment: bool = True,
    ) -> dict[str, Any] | None:
        kind = str(field_kind or "text").lower() or "text"
        if kind in {"file", "password"}:
            return None
        raw = " ".join(str(raw_question or "").split())
        normalized = normalize_question(raw)
        if not normalized:
            return None
        if source == "seed" and self.seed_source_name() != "seed":
            source = "manual"
        if source not in QUESTION_SOURCES:
            source = "unanswered"
        option_list = [str(option) for option in (options or []) if str(option).strip()][:50]
        hashed = option_set_hash(kind, option_list)
        proposed = redact_proposed_value(kind, proposed_value)
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO questions(
                normalized_question, raw_question, field_kind, options_json, option_set_hash,
                required, job_id, company, title, source, proposed_value, approved_value,
                approval_status, provenance, confidence, seen_count, reuse_count,
                last_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'pending', ?, ?, ?, 0, ?, ?)
            ON CONFLICT(normalized_question, field_kind, option_set_hash) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                seen_count = questions.seen_count + CASE WHEN ? THEN 1 ELSE 0 END,
                job_id = CASE WHEN excluded.job_id != '' THEN excluded.job_id ELSE questions.job_id END,
                company = CASE WHEN excluded.company != '' THEN excluded.company ELSE questions.company END,
                title = CASE WHEN excluded.title != '' THEN excluded.title ELSE questions.title END,
                required = CASE WHEN excluded.required != 0 THEN 1 ELSE questions.required END,
                proposed_value = CASE
                    WHEN questions.approval_status = 'approved' THEN questions.proposed_value
                    WHEN excluded.proposed_value != '' THEN excluded.proposed_value
                    ELSE questions.proposed_value
                END,
                source = CASE
                    WHEN questions.approval_status IN ('approved', 'rejected') THEN questions.source
                    WHEN excluded.source = 'mapped' THEN 'mapped'
                    WHEN excluded.source = 'llm' AND questions.source != 'mapped' THEN 'llm'
                    WHEN questions.source = 'unanswered' THEN excluded.source
                    ELSE questions.source
                END,
                provenance = CASE
                    WHEN questions.approval_status = 'approved' THEN questions.provenance
                    WHEN excluded.provenance != '' THEN excluded.provenance
                    ELSE questions.provenance
                END,
                confidence = CASE
                    WHEN questions.approval_status = 'approved' THEN questions.confidence
                    ELSE COALESCE(excluded.confidence, questions.confidence)
                END
            """,
            (
                normalized,
                raw,
                kind,
                json.dumps(option_list, ensure_ascii=True),
                hashed,
                1 if required else 0,
                str(job_id or ""),
                str(company or ""),
                str(title or ""),
                source,
                proposed,
                provenance,
                confidence,
                1,
                now,
                now,
                1 if increment else 0,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT * FROM questions
            WHERE normalized_question = ? AND field_kind = ? AND option_set_hash = ?
            """,
            (normalized, kind, hashed),
        ).fetchone()
        item = self._question_row(row)
        if item is None:
            return None
        status = str(item.get("classification_status") or "").strip().lower()
        if status in {"heuristic", "llm", "duplicate"} and str(item.get("classified_kind") or ""):
            return item
        try:
            from linkedin_easy_apply.question_classifier import apply_fast_classification

            return apply_fast_classification(self, item) or item
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            log.warning("question classification skipped: %s", exc.__class__.__name__)
            return item

    def get_question(self, question_id: int | str) -> dict[str, Any] | None:
        if question_id in (None, ""):
            return None
        text = str(question_id).strip()
        row = None
        if text.isdigit():
            row = self._conn.execute(
                "SELECT * FROM questions WHERE id = ?",
                (int(text),),
            ).fetchone()
        if row is None:
            row = self._conn.execute(
                """
                SELECT * FROM questions
                WHERE normalized_question = ?
                ORDER BY CASE
                    WHEN merged_into_id IS NULL OR merged_into_id = 0 THEN 0
                    ELSE 1
                END, seen_count DESC, id DESC
                LIMIT 1
                """,
                (normalize_question(text),),
            ).fetchone()
        return self._question_row(row)

    def list_pending_questions(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.list_questions(status="pending", limit=limit)

    def list_approved_questions(self, limit: int = 500) -> list[dict[str, Any]]:
        """Approved rows the retrieval sidecar should read. This is memory, not training."""
        rows = self._conn.execute(
            """
            SELECT * FROM questions
            WHERE approval_status = 'approved'
              AND TRIM(COALESCE(approved_value, '')) != ''
              AND """ + VISIBLE_QUESTION_SQL + """
            ORDER BY reuse_count DESC, seen_count DESC, last_seen_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [item for item in (self._question_row(row) for row in rows) if item]

    def list_approved_answers(self, limit: int = APPROVED_RETRIEVAL_POOL) -> list[dict[str, Any]]:
        return self.list_approved_questions(limit=limit)

    def retrieve_approved_answers(
        self,
        questions: list[dict[str, Any]],
        limit: int = APPROVED_PROMPT_LIMIT,
    ) -> list[dict[str, str]]:
        return match_approved_answers(
            self.list_approved_questions(limit=APPROVED_RETRIEVAL_POOL),
            questions,
            limit=limit,
        )

    def upsert_proposed_answer(
        self,
        question: str,
        value: str,
        *,
        source: str = "llm",
        field_kind: str = "",
        options: list[str] | None = None,
        job_id: str = "",
        company: str = "",
        title: str = "",
        confidence: float | None = None,
        provenance: str = "ollama",
        required: bool = False,
    ) -> dict[str, Any]:
        """Persist a typed LLM fill as pending. Never auto-approves, including sensitive fields."""
        if source not in QUESTION_SOURCES:
            source = "llm"
        row = self.upsert_question(
            raw_question=question,
            field_kind=field_kind or "text",
            options=options,
            required=required,
            job_id=str(job_id or ""),
            company=company,
            title=title,
            source=source,
            proposed_value=value,
            provenance=provenance or "ollama",
            confidence=confidence,
        )
        return row or {}

    def increment_reuse(self, question_id: int | str) -> None:
        row = self.get_question(question_id)
        if row is None:
            return
        self._conn.execute(
            """
            UPDATE questions
            SET reuse_count = COALESCE(reuse_count, 0) + 1, last_seen_at = ?
            WHERE id = ?
            """,
            (utc_now(), int(row["id"])),
        )
        self._conn.commit()

    def record_successful_fills(self, fills: list[dict[str, Any]]) -> None:
        """Increment reuse for approved or exact-mapped answers after a successful apply.

        LLM proposals stay pending. Sensitive LLM answers are never auto-approved.
        """
        for fill in fills or []:
            if not isinstance(fill, dict):
                continue
            question = str(fill.get("question") or "")
            source = str(fill.get("source") or "")
            mapped = source == "mapped" or bool(fill.get("mapped"))
            source_ids = [str(item) for item in (fill.get("source_ids") or []) if str(item).strip()]
            already_approved = bool(fill.get("already_approved"))
            qid = fill.get("question_id") if fill.get("question_id") not in (None, "") else fill.get("id")
            row = self.get_question(qid) if qid not in (None, "") else None
            if row is None and question:
                row = self.get_question(question)
            if mapped or already_approved:
                ids = set(source_ids)
                if row is not None and (
                    row.get("approval_status") == "approved" or mapped or row.get("source") == "mapped"
                ):
                    ids.add(str(row["id"]))
                for ident in ids:
                    self.increment_reuse(ident)
                continue
            if source == "llm":
                if is_sensitive_question(question):
                    for ident in source_ids:
                        self.increment_reuse(ident)
                    continue
                ids = set(source_ids)
                if row is not None and row.get("approval_status") == "approved":
                    ids.add(str(row["id"]))
                for ident in ids:
                    self.increment_reuse(ident)

    def approve_answer(self, question_id: int | str, value: str) -> dict[str, Any]:
        text = " ".join(str(value or "").split())
        if not text:
            raise ValueError("approved_value is required")
        existing = self.get_question(question_id)
        if existing is None:
            raise KeyError(question_id)
        for option in existing.get("options") or []:
            if str(option).strip().lower() == text.lower():
                text = str(option)
                break
        source = existing["source"]
        if source == "unanswered":
            source = "manual"
        self._conn.execute(
            """
            UPDATE questions
            SET approved_value = ?, approval_status = 'approved', source = ?,
                provenance = 'operator', last_seen_at = ?
            WHERE id = ?
            """,
            (text, source, utc_now(), int(existing["id"])),
        )
        self._conn.commit()
        row = self.get_question(int(existing["id"]))
        if row is None:
            raise KeyError(question_id)
        return row

    def approve_question(self, question_id: int | str, approved_value: str) -> dict[str, Any] | None:
        existing = self.get_question(question_id)
        if existing is None:
            return None
        return self.approve_answer(int(existing["id"]), approved_value)

    def reject_question(self, question_id: int | str) -> dict[str, Any] | None:
        existing = self.get_question(question_id)
        if existing is None:
            return None
        self._conn.execute(
            """
            UPDATE questions
            SET approval_status = 'rejected', last_seen_at = ?
            WHERE id = ?
            """,
            (utc_now(), int(existing["id"])),
        )
        self._conn.commit()
        return self.get_question(int(existing["id"]))

    def list_questions(
        self,
        status: str = "",
        query: str = "",
        kind: str = "",
        source: str = "",
        limit: int = 200,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            if status not in QUESTION_STATUSES:
                return []
            clauses.append("approval_status = ?")
            params.append(status)
        if kind:
            clauses.append(
                "LOWER(COALESCE(NULLIF(classified_kind, ''), field_kind)) = LOWER(?)"
            )
            params.append(kind)
        origin = str(source or "").strip().lower()
        if origin == "llm":
            clauses.append("source = 'llm'")
        elif origin == "seed":
            clauses.append("(source = 'seed' OR provenance = 'seed')")
        elif origin == "linkedin":
            clauses.append("source NOT IN ('llm', 'seed') AND IFNULL(provenance, '') != 'seed'")
        if query:
            like = "%" + query + "%"
            clauses.append(
                "(raw_question LIKE ? OR normalized_question LIKE ?"
                " OR cleaned_question LIKE ? OR approved_value LIKE ?"
                " OR proposed_value LIKE ? OR title LIKE ? OR company LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, like])
        if not include_hidden:
            clauses.append(VISIBLE_QUESTION_SQL)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            "SELECT * FROM questions" + where + " ORDER BY last_seen_at DESC, id DESC LIMIT ?",
            [*params, int(limit)],
        ).fetchall()
        return [item for item in (self._question_row(row) for row in rows) if item]

    def question_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT approval_status AS status, COUNT(*) AS n FROM questions"
            " WHERE " + VISIBLE_QUESTION_SQL + " GROUP BY approval_status"
        ).fetchall()
        counts = {status: 0 for status in QUESTION_STATUSES}
        total = 0
        for row in rows:
            status = str(row["status"])
            count = int(row["n"])
            if status in counts:
                counts[status] = count
            total += count
        counts["total"] = total
        return counts

    def question_origin_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT source, provenance FROM questions WHERE " + VISIBLE_QUESTION_SQL
        ).fetchall()
        counts = {origin: 0 for origin in QUESTION_ORIGINS}
        total = 0
        for row in rows:
            origin = question_origin(str(row["source"] or ""), str(row["provenance"] or ""))
            counts[origin] = counts.get(origin, 0) + 1
            total += 1
        counts["total"] = total
        return counts

    def question_kinds(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT COALESCE(NULLIF(classified_kind, ''), field_kind) AS kind
            FROM questions
            WHERE TRIM(COALESCE(NULLIF(classified_kind, ''), field_kind)) != ''
              AND """ + VISIBLE_QUESTION_SQL + """
            ORDER BY kind
            """
        ).fetchall()
        return [str(row["kind"]) for row in rows]

    def _needs_classification_sql(self, include_heuristic: bool) -> str:
        if include_heuristic:
            status_sql = (
                "(TRIM(IFNULL(classification_status, '')) IN ('', 'unclassified', 'heuristic')"
                " OR TRIM(IFNULL(classified_kind, '')) = '')"
            )
        else:
            status_sql = (
                "(TRIM(IFNULL(classification_status, '')) IN ('', 'unclassified')"
                " OR TRIM(IFNULL(classified_kind, '')) = '')"
            )
        contact_sql = (
            " OR ("
            " LOWER(COALESCE(NULLIF(classified_kind, ''), field_kind))"
            " IN ('select', 'radio')"
            " AND ("
            " LOWER(raw_question) LIKE '%email%'"
            " OR LOWER(raw_question) LIKE '%e-mail%'"
            " OR LOWER(IFNULL(cleaned_question, '')) LIKE '%email%'"
            " OR LOWER(IFNULL(options_json, '')) LIKE '%email%'"
            " OR LOWER(raw_question) LIKE '%phone%'"
            " OR LOWER(raw_question) LIKE '%mobile%'"
            " OR LOWER(raw_question) LIKE '%telephone%'"
            " OR LOWER(IFNULL(options_json, '')) LIKE '%phone%'"
            "))"
        )
        return (
            "(" + status_sql + contact_sql + ")"
            + " AND (merged_into_id IS NULL OR merged_into_id = 0)"
            + " AND LOWER(IFNULL(classification_status, '')) != 'duplicate'"
        )

    def save_question_classification(
        self,
        question_id: int | str | None,
        *,
        cleaned_question: str,
        classified_kind: str,
        answer_shape: str,
        cluster_key: str,
        classification_status: str,
        not_user_answerable: bool = False,
    ) -> dict[str, Any] | None:
        existing = self.get_question(question_id) if question_id not in (None, "") else None
        if existing is None:
            return None
        kind = str(classified_kind or "").strip().lower()
        proposed = str(existing.get("proposed_value") or "")
        if kind in {"email", "tel", "phone"} and proposed:
            proposed = redact_proposed_value(kind, proposed)
        status = str(classification_status or "heuristic").strip().lower() or "heuristic"
        self._conn.execute(
            """
            UPDATE questions
            SET cleaned_question = ?, classified_kind = ?, answer_shape = ?,
                cluster_key = ?, classification_status = ?, not_user_answerable = ?,
                proposed_value = ?
            WHERE id = ?
            """,
            (
                " ".join(str(cleaned_question or "").split()),
                kind,
                str(answer_shape or ""),
                str(cluster_key or ""),
                status,
                1 if not_user_answerable or kind == "file" else 0,
                proposed,
                int(existing["id"]),
            ),
        )
        self._conn.commit()
        return self.get_question(int(existing["id"]))

    def find_canonical_by_cluster(
        self,
        cluster_key: str,
        exclude_id: int | None = None,
    ) -> dict[str, Any] | None:
        key = str(cluster_key or "").strip()
        if not key:
            return None
        row = self._conn.execute(
            """
            SELECT * FROM questions
            WHERE cluster_key = ?
              AND id != ?
              AND (merged_into_id IS NULL OR merged_into_id = 0)
              AND LOWER(IFNULL(classification_status, '')) != 'duplicate'
            ORDER BY CASE WHEN approval_status = 'approved' THEN 0 ELSE 1 END,
                     seen_count DESC, id ASC
            LIMIT 1
            """,
            (key, int(exclude_id or 0)),
        ).fetchone()
        return self._question_row(row)

    def merge_question_into(self, loser_id: int, winner_id: int) -> dict[str, Any] | None:
        if int(loser_id) == int(winner_id):
            return self.get_question(winner_id)
        loser = self.get_question(loser_id)
        winner = self.get_question(winner_id)
        if loser is None or winner is None:
            return winner
        extra_seen = int(loser.get("seen_count") or 0)
        approved = str(winner.get("approved_value") or "").strip() or str(
            loser.get("approved_value") or ""
        ).strip()
        proposed = str(winner.get("proposed_value") or "").strip() or str(
            loser.get("proposed_value") or ""
        )
        cleaned = str(winner.get("cleaned_question") or "").strip() or str(
            loser.get("cleaned_question") or ""
        )
        classified = str(winner.get("classified_kind") or "").strip() or str(
            loser.get("classified_kind") or ""
        )
        shape = str(winner.get("answer_shape") or "").strip() or str(
            loser.get("answer_shape") or ""
        )
        cluster = str(winner.get("cluster_key") or "").strip() or str(
            loser.get("cluster_key") or ""
        )
        approval = str(winner.get("approval_status") or "pending")
        if approval != "approved" and str(loser.get("approval_status") or "") == "approved":
            approval = "approved"
        now = utc_now()
        self._conn.execute(
            """
            UPDATE questions
            SET seen_count = seen_count + ?, last_seen_at = ?,
                approved_value = CASE WHEN ? != '' THEN ? ELSE approved_value END,
                proposed_value = CASE WHEN ? != '' THEN ? ELSE proposed_value END,
                cleaned_question = CASE WHEN ? != '' THEN ? ELSE cleaned_question END,
                classified_kind = CASE WHEN ? != '' THEN ? ELSE classified_kind END,
                answer_shape = CASE WHEN ? != '' THEN ? ELSE answer_shape END,
                cluster_key = CASE WHEN ? != '' THEN ? ELSE cluster_key END,
                approval_status = ?,
                job_id = CASE WHEN job_id = '' THEN ? ELSE job_id END,
                company = CASE WHEN company = '' THEN ? ELSE company END,
                title = CASE WHEN title = '' THEN ? ELSE title END
            WHERE id = ?
            """,
            (
                extra_seen,
                now,
                approved,
                approved,
                proposed,
                proposed,
                cleaned,
                cleaned,
                classified,
                classified,
                shape,
                shape,
                cluster,
                cluster,
                approval,
                str(loser.get("job_id") or ""),
                str(loser.get("company") or ""),
                str(loser.get("title") or ""),
                int(winner["id"]),
            ),
        )
        self._conn.execute(
            """
            UPDATE questions
            SET merged_into_id = ?, classification_status = 'duplicate', last_seen_at = ?
            WHERE id = ?
            """,
            (int(winner["id"]), now, int(loser["id"])),
        )
        self._conn.commit()
        return self.get_question(int(winner["id"]))

    def merge_duplicate_question(
        self,
        question_id: int | str | None,
        *,
        cluster_key: str = "",
        duplicate_of_id: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_question(question_id) if question_id not in (None, "") else None
        if current is None:
            return None
        current_id = int(current["id"])
        canonical = None
        if duplicate_of_id not in (None, "", 0):
            hinted = self.get_question(int(duplicate_of_id))
            if hinted is not None:
                follow = hinted.get("merged_into_id")
                canonical = self.get_question(int(follow)) if follow not in (None, "", 0) else hinted
        if canonical is None:
            canonical = self.find_canonical_by_cluster(cluster_key, exclude_id=current_id)
        if canonical is None or int(canonical["id"]) == current_id:
            return current
        left, right = canonical, current
        left_score = (
            0 if str(left.get("approval_status") or "") == "approved" else 1,
            -int(left.get("seen_count") or 0),
            int(left.get("id") or 0),
        )
        right_score = (
            0 if str(right.get("approval_status") or "") == "approved" else 1,
            -int(right.get("seen_count") or 0),
            int(right.get("id") or 0),
        )
        winner, loser = (left, right) if left_score <= right_score else (right, left)
        return self.merge_question_into(int(loser["id"]), int(winner["id"]))

    def list_questions_needing_classification(
        self,
        limit: int = 8,
        *,
        include_heuristic: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM questions WHERE "
            + self._needs_classification_sql(include_heuristic)
            + " ORDER BY id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [item for item in (self._question_row(row) for row in rows) if item]

    def count_questions_needing_classification(self, *, include_heuristic: bool = False) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE "
            + self._needs_classification_sql(include_heuristic)
        ).fetchone()
        return int(row["n"] if row is not None else 0)

    def _ensure_operator_schema(self) -> None:
        self._conn.executescript(OPERATOR_DDL)

    def get_setting(self, key: str, default: str = "") -> str:
        name = str(key or "").strip()
        if not name:
            return default
        row = self._conn.execute(
            "SELECT value FROM operator_settings WHERE key = ?",
            (name,),
        ).fetchone()
        if row is None:
            return default
        return str(row["value"] if "value" in row else row[0] or default)

    def set_setting(self, key: str, value: str) -> None:
        name = str(key or "").strip()
        if not name:
            raise ValueError("setting key is required")
        self._conn.execute(
            """
            INSERT INTO operator_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (name, str(value if value is not None else ""), utc_now()),
        )

    def list_settings(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM operator_settings").fetchall()
        return {str(row["key"]): str(row["value"] or "") for row in rows}

    def add_chat_message(self, role: str, content: str, model: str = "") -> dict[str, Any]:
        allowed = {"system", "user", "assistant"}
        kind = str(role or "").strip().lower()
        if kind not in allowed:
            raise ValueError("chat role must be user or assistant")
        text = str(content or "").strip()
        if not text:
            raise ValueError("chat content is required")
        now = utc_now()
        cursor = self._conn.execute(
            "INSERT INTO chat_messages(created_at, role, content, model) VALUES (?, ?, ?, ?)",
            (now, kind, text[:8000], str(model or "")[:128]),
        )
        self._conn.execute(
            """
            DELETE FROM chat_messages WHERE id NOT IN (
                SELECT id FROM chat_messages ORDER BY id DESC LIMIT ?
            )
            """,
            (CHAT_HISTORY_LIMIT,),
        )
        return {
            "id": int(cursor.lastrowid or 0),
            "created_at": now,
            "role": kind,
            "content": text[:8000],
            "model": str(model or "")[:128],
        }

    def list_chat_messages(self, limit: int = 40) -> list[dict[str, Any]]:
        cap = max(1, min(int(limit or 40), CHAT_HISTORY_LIMIT))
        rows = self._conn.execute(
            """
            SELECT id, created_at, role, content, model
            FROM chat_messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        items = [
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"] or ""),
                "role": str(row["role"] or ""),
                "content": str(row["content"] or ""),
                "model": str(row["model"] or ""),
            }
            for row in rows
        ]
        items.reverse()
        return items

    def record_question(
        self,
        question_text: str,
        *,
        kind: str = "",
        options: list[str] | None = None,
        proposed_value: str = "",
        job_id: str = "",
        job_title: str = "",
        job_company: str = "",
    ) -> dict[str, Any]:
        row = self.upsert_question(
            raw_question=question_text,
            field_kind=kind or "text",
            options=options,
            job_id=job_id,
            company=job_company,
            title=job_title,
            source="unanswered",
            proposed_value=proposed_value,
        )
        if row is None:
            raise ValueError("question_text is required")
        return row


def _bind_store_connections() -> None:
    """Open a short-lived SQLite connection per public method; never hold one across HTTP."""
    for name in (
        "start_run",
        "finish_run",
        "restore_running_run",
        "upsert_job",
        "add_event",
        "upsert_question",
        "upsert_proposed_answer",
        "increment_reuse",
        "record_successful_fills",
        "approve_answer",
        "approve_question",
        "reject_question",
        "record_question",
        "save_question_classification",
        "merge_question_into",
        "merge_duplicate_question",
        "set_setting",
        "add_chat_message",
    ):
        setattr(Store, name, _store_tx(True)(getattr(Store, name)))
    for name in (
        "count_jobs",
        "current_run",
        "running_run",
        "get_job",
        "list_jobs",
        "list_events",
        "job_counts",
        "latest_job_for_run",
        "latest_job",
        "latest_failure",
        "job_metrics",
        "_table_columns",
        "seed_source_name",
        "question_identity_exists",
        "get_question",
        "list_pending_questions",
        "list_approved_questions",
        "list_approved_answers",
        "retrieve_approved_answers",
        "list_questions",
        "question_counts",
        "question_origin_counts",
        "question_kinds",
        "find_canonical_by_cluster",
        "list_questions_needing_classification",
        "count_questions_needing_classification",
        "get_setting",
        "list_settings",
        "list_chat_messages",
    ):
        setattr(Store, name, _store_tx(False)(getattr(Store, name)))


_bind_store_connections()
