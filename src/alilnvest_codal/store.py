"""Persistent incremental SQLite store for Codal datasource responses."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ABSENT_TTL_SECONDS = 6 * 3600
_SQLITE_BUSY_TIMEOUT_MS = 10_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasource (
    tracing_no INTEGER NOT NULL,
    sheet_id TEXT NOT NULL,
    symbol TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok', 'absent')),
    checked_at REAL NOT NULL,
    payload TEXT,
    PRIMARY KEY (tracing_no, sheet_id)
)
"""


@dataclass(frozen=True)
class StoredDatasource:
    status: str
    payload: dict | None
    checked_at: float


class SQLiteStore:
    """Thread-safe SQLite store in WAL mode."""

    def __init__(self, path: str | Path, *, absent_ttl: float = ABSENT_TTL_SECONDS) -> None:
        self.path = Path(path).expanduser()
        self.absent_ttl = float(absent_ttl)
        if not math.isfinite(self.absent_ttl):
            raise ValueError("absent_ttl must be finite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000)
        self._conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._conn:
            self._conn.execute(_SCHEMA)

    @staticmethod
    def _sheet(sheet_id: str | None) -> str:
        return "" if sheet_id is None else str(sheet_id)

    def get_datasource(self, tracing_no: int, sheet_id: str | None = None) -> StoredDatasource | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT status, checked_at, payload FROM datasource WHERE tracing_no = ? AND sheet_id = ?",
                (int(tracing_no), self._sheet(sheet_id)),
            ).fetchone()
            if row is None:
                return None
            status, checked_at, payload_text = row
            if status == "absent":
                if time.time() - checked_at > self.absent_ttl:
                    with self._conn:
                        self._conn.execute("DELETE FROM datasource WHERE tracing_no=? AND sheet_id=?",
                                           (int(tracing_no), self._sheet(sheet_id)))
                    return None
                return StoredDatasource("absent", None, checked_at)
            try:
                payload = json.loads(payload_text)
                if not isinstance(payload, dict):
                    raise ValueError("payload is not an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                # A corrupt row must not poison every future read.
                with self._conn:
                    self._conn.execute("DELETE FROM datasource WHERE tracing_no=? AND sheet_id=?",
                                       (int(tracing_no), self._sheet(sheet_id)))
                return None
            return StoredDatasource("ok", payload, checked_at)

    def put_datasource(self, tracing_no: int, sheet_id: str | None, payload: dict, *, symbol: str | None = None) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self._upsert(tracing_no, sheet_id, symbol, "ok", text)

    def put_absent(self, tracing_no: int, sheet_id: str | None, *, symbol: str | None = None) -> None:
        self._upsert(tracing_no, sheet_id, symbol, "absent", None)

    def count(self, status: str | None = None) -> int:
        with self._lock:
            if status is not None and status not in {"ok", "absent"}:
                raise ValueError("status must be 'ok', 'absent', or None")
            query = "SELECT COUNT(*) FROM datasource" if status is None else "SELECT COUNT(*) FROM datasource WHERE status=?"
            row = self._conn.execute(query, () if status is None else (status,)).fetchone()
            return int(row[0])

    def forget_absent(self) -> int:
        with self._lock, self._conn:
            return int(self._conn.execute("DELETE FROM datasource WHERE status='absent'").rowcount)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _upsert(self, tracing_no: int, sheet_id: str | None, symbol: str | None,
                status: str, payload: str | None) -> None:
        if status not in {"ok", "absent"}:
            raise ValueError("invalid datasource status")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO datasource (tracing_no,sheet_id,symbol,status,checked_at,payload) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(tracing_no,sheet_id) DO UPDATE SET symbol=excluded.symbol,status=excluded.status,"
                "checked_at=excluded.checked_at,payload=excluded.payload",
                (int(tracing_no), self._sheet(sheet_id), symbol, status, time.time(), payload),
            )
