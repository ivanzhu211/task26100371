"""Consistency vs an independent replay oracle, incl. reopen."""

import random
from datetime import date, timedelta

from vat_rates import Query, Revision, VatRateStore

from oracle import ReplayOracle

JURISDICTIONS = ["DE", "FR", "IT", "ES", "NL"]
EPOCH = date(2024, 1, 1)
SPAN_DAYS = 1100  # ~3 years of tax days


def _day(offset: int) -> date:
    return EPOCH + timedelta(days=offset)


def generate_publishes(seed: int = 20251003, batches: int = 300):
    """Publish sequence with retroactive edits, future-dated rates, nested
    intervals, clears and zero-rate restorations."""
    rng = random.Random(seed)
    batches_out = []
    for _ in range(batches):
        revisions = []
        # 1-3 distinct jurisdictions per batch keeps intervals of one
        # jurisdiction non-overlapping within the batch.
        for jur in rng.sample(JURISDICTIONS, rng.randint(1, 3)):
            kind = rng.random()
            anchor = rng.randint(0, SPAN_DAYS)
            if kind < 0.30:
                # retroactive: starts in the past relative to "now" of the
                # sequence (mid-range), short interval
                start = rng.randint(0, SPAN_DAYS // 2)
                length = rng.randint(1, 60)
            elif kind < 0.50:
                # future-dated, possibly open-ended
                start = rng.randint(SPAN_DAYS // 2, SPAN_DAYS)
                length = rng.randint(30, 400)
            elif kind < 0.70:
                # narrow interval nested inside a wide one
                start = max(0, anchor - 5)
                length = rng.randint(1, 10)
            else:
                # wide interval, often open-ended
                start = rng.randint(0, SPAN_DAYS - 200)
                length = rng.randint(100, 400)
            end = None if rng.random() < 0.15 else _day(start + length)
            r = rng.random()
            if r < 0.15:
                rate = None  # clear
            elif r < 0.25:
                rate = 0.0  # zero rate is a valid rate
            else:
                rate = round(rng.uniform(0.01, 0.30), 4)
            revisions.append(Revision(jur, _day(start), end, rate))
        batches_out.append(revisions)
    return batches_out


def generate_queries(seed: int, n: int, latest_version: int):
    rng = random.Random(seed)
    queries = []
    for _ in range(n):
        jur = rng.choice(JURISDICTIONS + ["XX"])  # XX: never published
        day = _day(rng.randint(-30, SPAN_DAYS + 30))  # incl. uncovered days
        # mix early and recent known versions, unsorted
        kv = rng.choice(
            [rng.randint(1, latest_version // 10), rng.randint(1, latest_version)]
        )
        queries.append(Query(jur, day, kv))
    return queries


def _assert_same(store: VatRateStore, oracle: ReplayOracle, queries) -> None:
    expected = oracle.query(queries)
    actual = store.query(queries)
    assert actual == expected


def test_store_matches_replay_oracle(tmp_path):
    db = tmp_path / "rates.db"
    oracle = ReplayOracle()
    batches = generate_publishes()
    queries = generate_queries(seed=99, n=4000, latest_version=len(batches))

    with VatRateStore.open(db) as store:
        for batch in batches:
            assert store.publish(batch) == oracle.publish(batch)
        _assert_same(store, oracle, queries)

    # Reopen: conclusions must be identical.
    with VatRateStore.open(db) as store:
        assert store.latest_version == oracle.latest_version
        _assert_same(store, oracle, queries)
