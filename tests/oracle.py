"""Independent naive in-memory replay oracle.

Replays publish records one by one and answers queries by brute force.
Used as the ground truth to validate VatRateStore.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from vat_rates import Query, QueryResult, Revision

_Record = Tuple[int, str, date, Optional[date], Optional[float]]


class ReplayOracle:
    def __init__(self) -> None:
        self._records: List[_Record] = []
        self._version = 0

    def publish(self, revisions: List[Revision]) -> int:
        self._version += 1
        for rev in revisions:
            self._records.append(
                (self._version, rev.jurisdiction, rev.start, rev.end, rev.rate)
            )
        return self._version

    @property
    def latest_version(self) -> int:
        return self._version

    def query_one(self, q: Query) -> QueryResult:
        best_version: Optional[int] = None
        best_rate: Optional[float] = None
        for version, jur, start, end, rate in self._records:
            if version > q.known_version or jur != q.jurisdiction:
                continue
            if start <= q.day and (end is None or q.day < end):
                if best_version is None or version > best_version:
                    best_version = version
                    best_rate = rate
        return QueryResult(rate=best_rate, source_version=best_version)

    def query(self, queries: List[Query]) -> List[QueryResult]:
        return [self.query_one(q) for q in queries]
