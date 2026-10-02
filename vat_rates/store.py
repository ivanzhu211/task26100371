"""SQLite-backed store for published VAT rate revisions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .models import Query, QueryResult, Revision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    version      INTEGER NOT NULL,
    jurisdiction TEXT    NOT NULL,
    start        TEXT    NOT NULL,  -- ISO date, inclusive
    "end"        TEXT,              -- ISO date, exclusive; NULL = open-ended
    rate         REAL               -- NULL = cleared interval
);
CREATE INDEX IF NOT EXISTS idx_revisions_covering
    ON revisions (jurisdiction, start, version, "end", rate);
"""

# Bare-column-with-MAX idiom (SQLite >= 3.7.11): ``rate`` is taken from the
# row that achieves MAX(version).  A single covering-index scan, no sort.
_LOOKUP_SQL = """
SELECT rate, MAX(version) FROM revisions
WHERE jurisdiction = ?
  AND version <= ?
  AND start <= ?
  AND ("end" IS NULL OR "end" > ?)
"""


class VatRateStore:
    """Append-only store of VAT rate revisions with versioned queries."""

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def open(cls, path: Union[str, Path]) -> "VatRateStore":
        """Open (creating if needed) the store at ``path``."""
        return cls(path)

    @property
    def latest_version(self) -> int:
        """The most recently published version (0 if none)."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'version'"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def publish(self, revisions: Iterable[Revision]) -> int:
        """Publish a batch of revisions; returns the new global version."""
        rows = []
        for rev in revisions:
            self._validate(rev)
            rows.append(
                (
                    rev.jurisdiction,
                    rev.start.isoformat(),
                    rev.end.isoformat() if rev.end is not None else None,
                    rev.rate,
                )
            )
        version = self.latest_version + 1
        with self._conn:  # commit on success, rollback on exception
            self._conn.executemany(
                "INSERT INTO revisions (version, jurisdiction, start, \"end\", rate)"
                " VALUES (?, ?, ?, ?, ?)",
                ((version, *row) for row in rows),
            )
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (version,),
            )
        return version

    def query(self, queries: Iterable[Query]) -> List[QueryResult]:
        """Resolve a batch of queries against published revisions."""
        results: List[QueryResult] = []
        cur = self._conn.cursor()
        for q in queries:
            row = cur.execute(
                _LOOKUP_SQL,
                (q.jurisdiction, q.known_version, q.day.isoformat(), q.day.isoformat()),
            ).fetchone()
            rate: Optional[float] = None
            source: Optional[int] = None
            if row is not None and row[1] is not None:
                source = int(row[1])
                rate = None if row[0] is None else float(row[0])
            results.append(QueryResult(rate=rate, source_version=source))
        return results

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "VatRateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _validate(rev: Revision) -> None:
        if not rev.jurisdiction:
            raise ValueError("jurisdiction must be non-empty")
        if rev.end is not None and rev.end <= rev.start:
            raise ValueError(
                f"end ({rev.end}) must be after start ({rev.start})"
            )
        if rev.rate is not None and rev.rate < 0:
            raise ValueError(f"rate must be >= 0, got {rev.rate}")
