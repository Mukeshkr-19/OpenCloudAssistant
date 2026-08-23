"""SQLite incident lifecycle for guarded self-heal.

ponytail: SQLite over JSON — concurrent writers + durable state without a
framework. Schema stays one table until query patterns demand more.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional


# Terminal / truthful lifecycle. CLOSED is not used for incomplete work.
STATES = (
    "DETECTED",
    "CAPTURED",
    "CLASSIFIED",
    "RECOVERING",
    "VALIDATING",
    "REVIEWING",
    "REPAIR_VALIDATED",
    "READY_FOR_PROMOTION",
    "PR_OPEN",
    "CI_RUNNING",
    "PROMOTED",
    "DEPLOYING",
    "DEPLOYED",
    "POST_DEPLOY_CANARY",
    "RECOVERED",
    "QUARANTINED",
    "ROLLBACK_REQUIRED",
    "ROLLED_BACK",
    "CANARY",  # pre-promotion synthetic canary
    "CANARY_FAILED",
    "VALIDATION_FAILED",
    "NO_ACTION_TRANSIENT",
    "REPAIR_ENGINE_UNAVAILABLE",
    "HUMAN_REQUIRED",
    "HUMAN_REQUIRED_SECURITY",
    "FAILED",
    "DISABLED",
)

# States that still count as "open" for dedup (do not create a sibling).
OPEN_STATES = tuple(
    s
    for s in STATES
    if s
    not in (
        "RECOVERED",
        "ROLLED_BACK",
        "FAILED",
        "NO_ACTION_TRANSIENT",
        "DISABLED",
        "QUARANTINED",
    )
)

# Recently closed signatures may reopen if the same failure reappears.
REOPENABLE = ("RECOVERED", "NO_ACTION_TRANSIENT", "ROLLED_BACK", "FAILED")


class IncidentStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    signature TEXT NOT NULL,
                    state TEXT NOT NULL,
                    tier INTEGER NOT NULL DEFAULT 0,
                    severity TEXT NOT NULL DEFAULT 'MEDIUM',
                    title TEXT NOT NULL,
                    sanitized_task TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Migrate older DBs missing occurrence_count.
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "occurrence_count" not in cols:
                conn.execute(
                    "ALTER TABLE incidents ADD COLUMN occurrence_count "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_incidents_sig ON incidents(signature)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS circuit (
                    key TEXT PRIMARY KEY,
                    window_start REAL NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS controller (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantine (
                    sha TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            conn.commit()

    def get_flag(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM controller WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO controller(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    def enabled(self) -> bool:
        return self.get_flag("enabled", "1") != "0"

    def set_enabled(self, on: bool) -> None:
        self.set_flag("enabled", "1" if on else "0")

    def quarantine_sha(self, sha: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO quarantine(sha, reason, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(sha) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                (sha, reason[:500], time.time()),
            )
            conn.commit()

    def is_quarantined(self, sha: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM quarantine WHERE sha=?", (sha,)
            ).fetchone()
            return row is not None

    def create(
        self,
        *,
        signature: str,
        title: str,
        sanitized_task: str,
        severity: str = "MEDIUM",
        tier: int = 0,
        meta: Optional[dict] = None,
    ) -> dict[str, Any]:
        now = time.time()
        incident_id = f"inc-{uuid.uuid4().hex[:12]}"
        meta = meta or {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incidents(
                    id, signature, state, tier, severity, title, sanitized_task,
                    created_at, updated_at, attempts, occurrence_count, meta_json
                ) VALUES (?, ?, 'DETECTED', ?, ?, ?, ?, ?, ?, 0, 1, ?)
                """,
                (
                    incident_id,
                    signature,
                    tier,
                    severity,
                    title,
                    sanitized_task,
                    now,
                    now,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            conn.execute(
                "INSERT INTO events(incident_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
                (incident_id, now, "DETECTED", title[:500]),
            )
            conn.commit()
        return self.get(incident_id)

    def find_open_by_signature(self, signature: str) -> Optional[dict[str, Any]]:
        placeholders = ",".join("?" * len(OPEN_STATES))
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM incidents
                WHERE signature=? AND state IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (signature, *OPEN_STATES),
            ).fetchone()
            return self._row(row) if row else None

    def find_recent_by_signature(
        self, signature: str, within_seconds: float
    ) -> Optional[dict[str, Any]]:
        """Most recent incident for signature (any state) within window."""
        cutoff = time.time() - within_seconds
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM incidents
                WHERE signature=? AND updated_at >= ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (signature, cutoff),
            ).fetchone()
            return self._row(row) if row else None

    def bump_occurrence(self, incident_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE incidents SET
                    occurrence_count = occurrence_count + 1,
                    updated_at=?
                WHERE id=?
                """,
                (now, incident_id),
            )
            conn.execute(
                "INSERT INTO events(incident_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
                (incident_id, now, "REOCCURRENCE", "signature seen again"),
            )
            conn.commit()
        return self.get(incident_id)

    def get(self, incident_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            return self._row(row) if row else None

    def list_incidents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row(r) for r in rows]

    def transition(
        self,
        incident_id: str,
        state: str,
        *,
        detail: str = "",
        tier: Optional[int] = None,
        error: str = "",
        meta_update: Optional[dict] = None,
        bump_attempt: bool = False,
    ) -> dict[str, Any]:
        if state not in STATES:
            raise ValueError(f"invalid state: {state}")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if not row:
                raise KeyError(incident_id)
            meta = json.loads(row["meta_json"] or "{}")
            if meta_update:
                meta.update(meta_update)
            attempts = int(row["attempts"]) + (1 if bump_attempt else 0)
            conn.execute(
                """
                UPDATE incidents SET
                    state=?,
                    updated_at=?,
                    tier=COALESCE(?, tier),
                    last_error=?,
                    attempts=?,
                    meta_json=?
                WHERE id=?
                """,
                (
                    state,
                    now,
                    tier,
                    error or row["last_error"],
                    attempts,
                    json.dumps(meta, ensure_ascii=False),
                    incident_id,
                ),
            )
            conn.execute(
                "INSERT INTO events(incident_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
                (incident_id, now, state, (detail or error)[:2000]),
            )
            conn.commit()
        return self.get(incident_id)

    def events(self, incident_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE incident_id=? ORDER BY id ASC",
                (incident_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def circuit_count(self, key: str, window_seconds: float) -> int:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT window_start, count FROM circuit WHERE key=?", (key,)
            ).fetchone()
            if not row or (now - float(row["window_start"])) > window_seconds:
                return 0
            return int(row["count"])

    def circuit_bump(self, key: str, window_seconds: float) -> int:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT window_start, count FROM circuit WHERE key=?", (key,)
            ).fetchone()
            if not row or (now - float(row["window_start"])) > window_seconds:
                conn.execute(
                    "INSERT INTO circuit(key, window_start, count) VALUES (?, ?, 1) "
                    "ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start, "
                    "count=1",
                    (key, now),
                )
                conn.commit()
                return 1
            count = int(row["count"]) + 1
            conn.execute(
                "UPDATE circuit SET count=? WHERE key=?", (count, key)
            )
            conn.commit()
            return count

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        return d
