"""Event ledger (kernel Phase 1, docs/KERNEL_PLAN.md).

The durable sink behind the event bus: every published :class:`JarvisEvent` is
recorded here, giving the queryable, correlated trail that is Phase 1's whole
point. Records are buffered in memory (cheap, safe to call from an event-loop
callback) and written to SQLite in batches by :meth:`flush`, which does the
blocking I/O and is meant to run off the loop (an executor job on a scheduler
tick, and once more on unload). This keeps a high-frequency source like
``state_changed`` from ever blocking the loop on a per-event write.

Storage goes through ``kernel.persistence`` so the ledger opens connections and
sequences its schema the one canonical way. It is its own database file; no
existing store is touched.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import List

from . import persistence
from .event import JarvisEvent

_LOGGER = logging.getLogger(__name__)

# Schema migrations, applied in order exactly once (see persistence.run_migrations).
_MIGRATIONS = [
    lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        " id TEXT PRIMARY KEY,"
        " ts REAL NOT NULL,"
        " type TEXT NOT NULL,"
        " source TEXT NOT NULL,"
        " subject TEXT,"
        " location TEXT,"
        " data TEXT NOT NULL DEFAULT '{}',"
        " confidence REAL,"
        " importance REAL,"
        " causality TEXT NOT NULL DEFAULT '[]',"
        " correlation_id TEXT)"
    ),
    lambda conn: conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts)"),
    lambda conn: conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (type)"),
    lambda conn: conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_corr ON events (correlation_id)"),
]


class EventLedger:
    """Buffers JarvisEvents and flushes them to SQLite in batches.

    ``record`` is cheap and loop-safe; ``flush`` does the blocking write and
    should be called off the event loop. If the buffer exceeds ``max_buffer``
    (a storm with no flush), the oldest entries are dropped and counted rather
    than growing without bound — the ledger is a shadow trail, never a liability.
    """

    def __init__(self, db_path: str, *, max_buffer: int = 5000) -> None:
        self._db_path = db_path
        self._max_buffer = max_buffer
        self._buffer: List[JarvisEvent] = []
        self._lock = threading.Lock()
        self._dropped = 0
        self._schema_ready = False

    # ── write path ───────────────────────────────────────────────────────────
    def record(self, event: JarvisEvent) -> None:
        """Buffer one event. Cheap and non-blocking; safe on the event loop."""
        with self._lock:
            self._buffer.append(event)
            overflow = len(self._buffer) - self._max_buffer
            if overflow > 0:
                del self._buffer[:overflow]
                self._dropped += overflow

    def flush(self) -> int:
        """Write buffered events to SQLite in one transaction. Returns the count.

        Blocking I/O — call off the event loop. On a write error the drained
        events are restored to the front of the buffer so nothing is silently lost.
        """
        with self._lock:
            if not self._buffer:
                return 0
            batch = self._buffer
            self._buffer = []
        try:
            self._ensure_schema()
            with persistence.transaction(self._db_path) as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO events "
                    "(id, ts, type, source, subject, location, data, confidence, "
                    " importance, causality, correlation_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [self._row(e) for e in batch],
                )
            return len(batch)
        except Exception as exc:
            with self._lock:  # put the batch back; try again on the next flush
                self._buffer[:0] = batch
                overflow = len(self._buffer) - self._max_buffer
                if overflow > 0:
                    del self._buffer[:overflow]
                    self._dropped += overflow
            _LOGGER.debug("event ledger: flush failed, %d event(s) requeued: %s",
                          len(batch), exc)
            return 0

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            persistence.run_migrations(self._db_path, _MIGRATIONS)
            self._schema_ready = True

    @staticmethod
    def _row(e: JarvisEvent) -> tuple:
        return (
            e.id, e.ts, e.type, e.source, e.subject, e.location,
            json.dumps(e.data, default=str, sort_keys=True),
            e.confidence, e.importance,
            json.dumps(list(e.causality)),
            e.correlation_id,
        )

    # ── read path (the queryable trail) ────────────────────────────────────────
    def query_recent(self, limit: int = 50) -> List[dict]:
        """Most recent events, newest first. Flushes pending writes first."""
        self.flush()
        return self._query(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (int(limit),))

    def query_by_correlation(self, correlation_id: str, limit: int = 200) -> List[dict]:
        """All events in one correlation chain, oldest first."""
        self.flush()
        return self._query(
            "SELECT * FROM events WHERE correlation_id = ? ORDER BY ts ASC LIMIT ?",
            (str(correlation_id), int(limit)))

    def _query(self, sql: str, params: tuple) -> List[dict]:
        try:
            self._ensure_schema()
            conn = persistence.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            _LOGGER.debug("event ledger: query failed: %s", exc)
            return []
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        for k, default in (("data", {}), ("causality", [])):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else default
            except Exception:
                d[k] = default
        return d

    # ── diagnostics ────────────────────────────────────────────────────────────
    @property
    def dropped(self) -> int:
        return self._dropped

    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)
