from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator
from uuid import uuid4

from ..sensitive_access import parse_iso_date

PATIENT = "patient"
FEEDBACK = "feedback"

OPEN = "open"
SUBMITTED = "submitted"
FINALIZED = "finalized"
CANCELLED = "cancelled"
EXPIRED = "expired"



def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """Additive observability columns. Idempotent; a concurrent initialiser that added the column
    between our check and ALTER is handled by the caller's retry on "duplicate column name"."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(turns)")}
    if "finalized_at" not in columns:
        connection.execute("ALTER TABLE turns ADD COLUMN finalized_at REAL")
    if "mode" not in columns:
        connection.execute("ALTER TABLE turns ADD COLUMN mode TEXT NOT NULL DEFAULT 'unknown'")


def _initialise(connect, schema: str, attempts: int = 50) -> None:
    import time as _time

    for attempt in range(attempts):
        try:
            with connect() as connection:
                if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(schema)
                _add_missing_columns(connection)
            return
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "duplicate column name" in message:
                # Another process (e.g. API and MCP sharing one store) migrated concurrently.
                continue
            if "locked" not in message or attempt == attempts - 1:
                raise
            _time.sleep(0.05)


class TurnError(Exception):
    """Raised for any rejected turn credential or transition. Messages are safe to show to a model."""

    def __init__(self, code: str, message: str, turn_id: str | None = None):
        super().__init__(message)
        self.code = code
        # Set only after the token secret has been verified, so audit rows cannot be forged.
        self.turn_id = turn_id


@dataclass(frozen=True)
class Turn:
    turn_id: str
    kind: str
    owner_id: str
    session_id: str
    case_id: str
    case_version: int
    request_id: str
    state_version: int
    question: str
    candidate_fact_ids: tuple[str, ...]
    status: str
    expires_at: float
    submission: Any
    created_at: float
    cancel_reason: str | None = None
    finalized_at: float | None = None
    mode: str = "unknown"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    case_version INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    question TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at REAL NOT NULL,
    submission_json TEXT,
    cancel_reason TEXT,
    created_at REAL NOT NULL,
    finalized_at REAL,
    mode TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, kind, created_at);
CREATE TABLE IF NOT EXISTS turn_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_calls_turn ON turn_calls(turn_id);
CREATE TABLE IF NOT EXISTS turn_boundaries (
    boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    semantic TEXT NOT NULL,
    timing_source TEXT NOT NULL,
    at REAL NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_boundaries_turn ON turn_boundaries(turn_id);
"""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _round_ms(value: float | None) -> int | None:
    return None if value is None else round(value * 1000)


def _p95(values: list[float]) -> float | None:
    """Nearest-rank 95th percentile (ceil(0.95*n) as a 1-based rank) — no interpolation claims."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _aggregate(turn_rows: list[sqlite3.Row], call_rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Shared aggregation used by the in-process ``TurnStore.stats`` and the read-only ``read_stats``.

    Semantics (do not overstate):
    - Coverage is recorded *trusted turns* only — the Nexent adapter path issues a turn; the
      rule-simulator path returns a selection directly and never issues one.
    - ``mode`` is the adapter mode recorded at issue time ("nexent" means the Nexent adapter was
      configured, NOT that a real vendor was called — a local fake Nexent uses the same mode).
    - ``tool_calls_failure_rate`` is a tool-call failure rate, not a model-request failure rate.
    - ``turn_completion`` is the wall-clock interval created_at -> finalized_at of one turn; it is
      not first-word, model-inference or browser end-to-end latency. ``p95`` is the nearest-rank
      percentile over the same recorded intervals.
    - Negative intervals (wall clock moved backwards) are counted separately, never clamped to zero.
    - ``by_case`` counts by the internal case id recorded at issue time; no case titles live here.
    - ``turns_today`` counts turns issued since the server-local calendar-day boundary.
    - token usage and cost stay "unknown"; nothing is invented.
    """
    turns_total = len(turn_rows)
    by_kind: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_case: dict[str, int] = {}
    durations: list[float] = []
    negative_count = 0
    for row in turn_rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_mode[row["mode"]] = by_mode.get(row["mode"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        case_id = row["case_id"]
        if case_id:
            by_case[case_id] = by_case.get(case_id, 0) + 1
        if row["finalized_at"] is not None and row["created_at"] is not None:
            duration = row["finalized_at"] - row["created_at"]
            if duration < 0:
                negative_count += 1
            else:
                durations.append(duration)
    calls_total = len(call_rows)
    calls_failed = sum(1 for row in call_rows if not row["ok"])
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    local_midnight = midnight.timestamp()
    next_midnight = (midnight + timedelta(days=1)).timestamp()
    turns_today = sum(1 for row in turn_rows if row["created_at"] is not None
                      and local_midnight <= row["created_at"] < next_midnight)
    return {
        "coverage": "recorded trusted turns only (Nexent adapter path); the simulator path returns a "
                    "selection directly and never issues a turn",
        "adapter_mode_semantics": "nexent means the Nexent adapter was configured, not that a real "
                                  "vendor was called; a local fake Nexent uses the same mode",
        "turns_total": turns_total,
        "turns_today": turns_today,
        "turns_today_boundary": "server-local calendar day",
        "by_kind": by_kind,
        "by_adapter_mode": by_mode,
        "by_status": by_status,
        "by_case": by_case,
        "tool_calls_total": calls_total,
        "tool_calls_failed": calls_failed,
        "tool_calls_failure_rate": round(calls_failed / calls_total, 4) if calls_total else None,
        "turn_completion": {
            "measured_count": len(durations),
            "negative_count": negative_count,
            "min_ms": _round_ms(min(durations)) if durations else None,
            "median_ms": _round_ms(statistics.median(durations)) if durations else None,
            "p95_ms": _round_ms(_p95(durations)),
            "max_ms": _round_ms(max(durations)) if durations else None,
            "p95_method": "nearest-rank",
        },
        "token_usage": "unknown",
        "cost": "unknown",
    }


class TurnStore:
    """Registry of backend-issued, short-lived, single-purpose turn credentials.

    A turn binds owner, session, case, case version, request id and the backend-computed
    candidate set. The model only ever holds the opaque token; every identity claim is
    resolved here, never taken from tool arguments. Stored in a separate SQLite file so the
    protected session database is never altered.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        # WAL is persistent in the file; switch once here. Switching needs an exclusive lock and does not
        # wait on the busy handler, so doing it on every connection fails under concurrent requests.
        # Schema creation and the additive observability migration are inside the same retry loop, so a
        # concurrent initialiser (API vs MCP on one old store) cannot fail on a duplicate column.
        _initialise(self._connect, _SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> Turn:
        submission = json.loads(row["submission_json"]) if row["submission_json"] else None
        return Turn(
            turn_id=row["turn_id"],
            kind=row["kind"],
            owner_id=row["owner_id"],
            session_id=row["session_id"],
            case_id=row["case_id"],
            case_version=row["case_version"],
            request_id=row["request_id"],
            state_version=row["state_version"],
            question=row["question"],
            candidate_fact_ids=tuple(json.loads(row["candidates_json"])),
            status=row["status"],
            expires_at=row["expires_at"],
            submission=submission,
            created_at=row["created_at"],
            cancel_reason=row["cancel_reason"],
            finalized_at=row["finalized_at"],
            mode=row["mode"],
        )

    def issue(
        self,
        *,
        kind: str,
        owner_id: str,
        session_id: str,
        case_id: str,
        case_version: int,
        request_id: str,
        state_version: int,
        question: str = "",
        candidate_fact_ids: Iterable[str] = (),
        ttl_s: float = 120.0,
        now: float | None = None,
        mode: str = "unknown",
    ) -> tuple[Turn, str]:
        if kind not in {PATIENT, FEEDBACK}:
            raise ValueError("unknown turn kind")
        now = time.time() if now is None else now
        turn_id = uuid4().hex
        secret = secrets.token_urlsafe(24)
        with self.lock, self._connect() as connection:
            # Replace the live turn atomically across API and MCP processes.
            connection.execute("BEGIN IMMEDIATE")
            # Only one live turn per (session, kind): the previous one can no longer act.
            connection.execute(
                "UPDATE turns SET status=?, cancel_reason=? WHERE session_id=? AND kind=? AND status IN (?, ?)",
                (CANCELLED, "superseded", session_id, kind, OPEN, SUBMITTED),
            )
            connection.execute(
                "INSERT INTO turns(turn_id, secret_hash, kind, owner_id, session_id, case_id, case_version, "
                "request_id, state_version, question, candidates_json, status, expires_at, created_at, mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id, _hash(secret), kind, owner_id, session_id, case_id, case_version,
                    request_id, state_version, question, json.dumps(list(candidate_fact_ids)),
                    OPEN, now + ttl_s, now, mode,
                ),
            )
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            connection.commit()
        return self._row_to_turn(row), f"{turn_id}.{secret}"

    def resolve(self, token: str, *, kind: str, allow: Iterable[str] = (OPEN,), now: float | None = None) -> Turn:
        """Return the live turn for a token or raise TurnError with a model-safe message."""
        now = time.time() if now is None else now
        if not isinstance(token, str) or "." not in token or len(token) > 200:
            raise TurnError("invalid_token", "turn token is invalid")
        turn_id, _, secret = token.partition(".")
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if row is None or not hmac.compare_digest(row["secret_hash"], _hash(secret)):
                raise TurnError("invalid_token", "turn token is invalid")
            if row["kind"] != kind:
                raise TurnError("wrong_kind", "turn token is not valid for this tool", turn_id)
            status = row["status"]
            if status in {OPEN, SUBMITTED} and row["expires_at"] < now:
                connection.execute(
                    "UPDATE turns SET status=?, cancel_reason=? WHERE turn_id=? AND status IN (?, ?)",
                    (EXPIRED, "ttl", turn_id, OPEN, SUBMITTED),
                )
                status = EXPIRED
            if status not in set(allow):
                raise TurnError(f"turn_{status}", f"turn is {status}", turn_id)
            if status == EXPIRED:
                raise TurnError("turn_expired", "turn is expired", turn_id)
            refreshed = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return self._row_to_turn(refreshed)

    def submit(self, turn_id: str, submission: Any) -> bool:
        """Atomically move open -> submitted. Returns False if the turn was no longer open."""
        with self.lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE turns SET status=?, submission_json=? WHERE turn_id=? AND status=? AND expires_at>=?",
                (SUBMITTED, json.dumps(submission, ensure_ascii=False), turn_id, OPEN, time.time()),
            )
            return updated.rowcount == 1

    def finalize(self, turn_id: str) -> bool:
        with self.lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE turns SET status=?, finalized_at=? WHERE turn_id=? AND status=? AND expires_at>=?",
                (FINALIZED, time.time(), turn_id, SUBMITTED, time.time()),
            )
            return updated.rowcount == 1

    def cancel(self, turn_id: str, reason: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE turns SET status=?, cancel_reason=? WHERE turn_id=? AND status IN (?, ?)",
                (CANCELLED, reason, turn_id, OPEN, SUBMITTED),
            )

    def get(self, turn_id: str) -> Turn:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        return self._row_to_turn(row)

    def record_call(self, turn_id: str, tool: str, ok: bool, detail: str = "") -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO turn_calls(turn_id, tool, ok, detail, at) VALUES (?,?,?,?,?)",
                (turn_id, tool, 1 if ok else 0, detail[:200], time.time()),
            )

    def record_boundary(
        self,
        turn_id: str,
        request_id: str,
        semantic: str,
        timing_source: str = "mcp_process_wall_clock",
        detail: str = "",
        at: float | None = None,
    ) -> None:
        """Record a semantic boundary event for a turn (e.g. submit_fact_selection committed)."""
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO turn_boundaries(turn_id, request_id, semantic, timing_source, at, detail) "
                "VALUES (?,?,?,?,?,?)",
                (turn_id, request_id, semantic, timing_source, at if at is not None else time.time(), detail[:200]),
            )

    def boundaries(self, turn_id: str) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT boundary_id, turn_id, request_id, semantic, timing_source, at, detail "
                "FROM turn_boundaries WHERE turn_id=? ORDER BY boundary_id",
                (turn_id,),
            ).fetchall()
        return [
            {
                "boundary_id": r["boundary_id"],
                "turn_id": r["turn_id"],
                "request_id": r["request_id"],
                "semantic": r["semantic"],
                "timing_source": r["timing_source"],
                "at": r["at"],
                "detail": r["detail"],
            }
            for r in rows
        ]

    def calls(self, turn_id: str) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT tool, ok, detail, at FROM turn_calls WHERE turn_id=? ORDER BY call_id", (turn_id,)
            ).fetchall()
        return [{"tool": r["tool"], "ok": bool(r["ok"]), "detail": r["detail"], "at": r["at"]} for r in rows]

    def stats(self, owner_ids: Iterable[str] | None = None, case_ids: Iterable[str] | None = None,
              date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        """Observability aggregate over recorded turns and tool calls (see ``_aggregate``).

        ``owner_ids`` restricts both the turn rows and the tool-call rows to the same set of owners,
        so a scoped aggregate never mixes per-turn counts with full-store tool counts. ``case_ids``
        and the UTC ``date_from``/``date_to`` day range (inclusive) filter the same rows; the
        tool-call rows always follow the turns that remain.
        """
        filters = _validate_filters(case_ids, date_from, date_to)
        with self.lock, self._connect() as connection:
            turn_rows, call_rows = _query_filtered(connection, owner_ids, "mode", "finalized_at", *filters)
        return _aggregate(turn_rows, call_rows)


def empty_stats() -> dict[str, Any]:
    """The aggregate for a store with no recorded turns (used when the store is absent/uninitialised)."""
    return _aggregate([], [])


def _validate_filters(case_ids: Iterable[str] | None, date_from: str | None,
                      date_to: str | None) -> tuple[list[str] | None, float | None, float | None]:
    """Normalise case ids and the UTC day range into (case_ids, from_ts, to_ts).

    ``date_from`` is inclusive and ``date_to`` inclusive as calendar days (the SQL bound for
    ``date_to`` is the start of the following UTC day). Invalid values raise ``ValueError`` with a
    client-safe message.
    """
    normalised_cases: list[str] | None = None
    if case_ids is not None:
        normalised_cases = [str(value) for value in dict.fromkeys(case_ids)]
        if not normalised_cases or any(not value.strip() or len(value) > 128 for value in normalised_cases):
            raise ValueError("病例筛选无效")
    from_ts = to_ts = None
    if date_from is not None:
        from_ts = datetime.combine(parse_iso_date(date_from, "date_from"),
                                   datetime.min.time(), tzinfo=timezone.utc).timestamp()
    if date_to is not None:
        if date_to == "9999-12-31":
            raise ValueError("date_to 须早于 9999-12-31")
        to_ts = datetime.combine(parse_iso_date(date_to, "date_to") + timedelta(days=1),
                                 datetime.min.time(), tzinfo=timezone.utc).timestamp()
    if from_ts is not None and to_ts is not None and from_ts >= to_ts:
        raise ValueError("日期范围无效：date_from 必须早于 date_to")
    return normalised_cases, from_ts, to_ts


def _query_filtered(connection: sqlite3.Connection, owner_ids: Iterable[str] | None,
                    mode_expr: str, finalized_expr: str,
                    case_ids: list[str] | None = None, from_ts: float | None = None,
                    to_ts: float | None = None) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Return (turn_rows, call_rows) with optional owner/case/day filters; tool calls follow the turns."""
    ids = None if owner_ids is None else list(dict.fromkeys(owner_ids))
    conditions: list[str] = []
    params: list[Any] = []
    if ids is not None:
        if not ids:
            return [], []
        conditions.append(f"owner_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if case_ids is not None:
        if not case_ids:
            return [], []
        conditions.append(f"case_id IN ({','.join('?' * len(case_ids))})")
        params.extend(case_ids)
    if from_ts is not None:
        conditions.append("created_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("created_at < ?")
        params.append(to_ts)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    turn_rows = connection.execute(
        f"SELECT kind, case_id, {mode_expr} AS mode, status, created_at, {finalized_expr} AS finalized_at "
        f"FROM turns{where}", params
    ).fetchall()
    call_rows = connection.execute(
        f"SELECT ok FROM turn_calls WHERE turn_id IN (SELECT turn_id FROM turns{where})", params
    ).fetchall()
    return turn_rows, call_rows


def read_stats(path: str | Path, owner_ids: Iterable[str] | None = None,
               case_ids: Iterable[str] | None = None, date_from: str | None = None,
               date_to: str | None = None) -> dict[str, Any]:
    """Read-only aggregate over an *existing* turn store.

    Never creates the file, never initialises tables, never switches journal mode and never
    migrates. ``owner_ids`` scopes both turn and tool-call rows to the same owners; ``case_ids``
    and the UTC ``date_from``/``date_to`` day range filter the same rows. Every failure mode is
    reported with a short, client-safe ``reason``/``error`` that carries no filesystem path or SQL
    detail (diagnostics stay server-side): a missing path, a non-SQLite file, a store without the
    ``turns`` table, a store missing ``turn_calls``, and a store predating the observability columns.

    Returns ``{"ok": True, "stats": {...}}`` or ``{"ok": False, "reason": ..., "error": ...}``.
    """
    filters = _validate_filters(case_ids, date_from, date_to)
    path = str(path)
    if not Path(path).is_file():
        return {"ok": False, "reason": "not_found", "error": "turn store not found"}
    connection = None
    try:
        connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='turns'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return {"ok": False, "reason": "corrupted", "error": "turn store is not a valid database"}
        if table is None:
            return {"ok": False, "reason": "not_initialized", "error": "turn store has no turns table"}
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(turns)")}
        missing = [name for name in ("finalized_at", "mode") if name not in columns]
        mode_expr = "mode" if "mode" in columns else "'unknown'"
        finalized_expr = "finalized_at" if "finalized_at" in columns else "NULL"
        try:
            turn_rows, call_rows = _query_filtered(connection, owner_ids, mode_expr, finalized_expr, *filters)
        except sqlite3.OperationalError:
            return {"ok": False, "reason": "incomplete", "error": "turn store is missing a required table"}
    finally:
        if connection is not None:
            connection.close()
    stats = _aggregate(turn_rows, call_rows)
    if missing:
        stats["schema_note"] = (
            "store predates observability columns; start the application once to migrate "
            "(this read-only tool never migrates)"
        )
    return {"ok": True, "stats": stats}
