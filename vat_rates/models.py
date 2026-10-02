"""Data types for the historical VAT rate library.

Dates are jurisdiction-local tax dates supplied by the caller; this library
performs no timezone handling.  ``end=None`` means "open-ended" (infinite
future).  ``rate=None`` on a revision means "clear" (no applicable rate in
that interval); note that a zero rate is a valid rate and is distinct from
a cleared interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Revision:
    """A single rate revision published as part of one batch.

    Covers the half-open interval [start, end).  ``rate`` is the new VAT
    rate (e.g. 0.19 for 19%); ``rate=None`` clears any applicable rate in
    the interval.
    """

    jurisdiction: str
    start: date
    end: Optional[date]
    rate: Optional[float]

    @property
    def is_clear(self) -> bool:
        return self.rate is None


@dataclass(frozen=True)
class Query:
    """A point-in-time, point-in-knowledge lookup.

    ``known_version`` restricts the answer to revisions published in
    versions <= known_version.
    """

    jurisdiction: str
    day: date
    known_version: int


@dataclass(frozen=True)
class QueryResult:
    """Outcome of a Query.

    ``rate`` is the applicable VAT rate, or None when no rate applies
    (the day was cleared, or never covered).  ``source_version`` is the
    publish version whose revision determined the outcome -- including a
    clear marker; it is None only when no published revision (within the
    known version) covers the day at all.
    """

    rate: Optional[float]
    source_version: Optional[int]

    @property
    def has_rate(self) -> bool:
        return self.rate is not None
