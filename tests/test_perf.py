"""Full-scale test: 50 jurisdictions, 20k versions, 200k revisions.

Skewed distribution: JUR-00 alone holds 100k revisions.  Verifies
correctness against the replay oracle, plus the non-functional budgets:

* reopen + first 2000 cross-version queries <= 3 s (early versions too)
* peak RSS <= 256 MiB
* persisted data <= 512 MiB
"""

import os
import random
import resource
import sys
import time
from datetime import date, timedelta

from vat_rates import Query, Revision, VatRateStore

from oracle import ReplayOracle

N_JURISDICTIONS = 50
N_VERSIONS = 20_000
SKEWED = "JUR-00"
SKEWED_PER_BATCH = 5            # 5 * 20k = 100k revisions for JUR-00
OTHERS_PER_BATCH = 5            # + 5 * 20k = 100k across the other 49
EPOCH = date(2020, 1, 1)
SPAN_DAYS = 3650
N_QUERIES = 2_000
TIME_BUDGET_S = 3.0
MEM_BUDGET_MIB = 256
SIZE_BUDGET_MIB = 512

JURISDICTIONS = [f"JUR-{i:02d}" for i in range(N_JURISDICTIONS)]
DATES = [EPOCH + timedelta(days=i) for i in range(SPAN_DAYS + 1)]


def _rate(rng: random.Random):
    r = rng.random()
    if r < 0.08:
        return None  # clear
    if r < 0.16:
        return 0.0   # zero rate
    return round(rng.uniform(0.01, 0.30), 4)


def _disjoint_intervals(rng, count):
    """Non-overlapping [start, end) intervals for one jurisdiction in one
    batch; mixes overlapping-across-versions, nested and disjoint shapes."""
    starts = sorted(rng.sample(range(SPAN_DAYS), count))
    intervals = []
    for i, s in enumerate(starts):
        limit = starts[i + 1] - s if i + 1 < len(starts) else SPAN_DAYS - s
        if rng.random() < 0.10:
            end = None  # open-ended
        else:
            length = rng.randint(1, max(1, min(limit, 400)))
            end = DATES[min(s + length, SPAN_DAYS)]
        intervals.append((DATES[s], end))
    return intervals


def generate_batches():
    rng = random.Random(710003)
    for _ in range(N_VERSIONS):
        revisions = [
            Revision(SKEWED, s, e, _rate(rng))
            for s, e in _disjoint_intervals(rng, SKEWED_PER_BATCH)
        ]
        for jur in rng.sample(JURISDICTIONS[1:], OTHERS_PER_BATCH):
            (s, e), = _disjoint_intervals(rng, 1)
            revisions.append(Revision(jur, s, e, _rate(rng)))
        yield revisions


def generate_queries(seed, n, latest_version, early_only=False):
    rng = random.Random(seed)
    queries = []
    for _ in range(n):
        jur = rng.choice(JURISDICTIONS)
        day = DATES[rng.randint(0, SPAN_DAYS)]
        if rng.random() < 0.10:
            day += timedelta(days=rng.randint(365, 3650))  # uncovered future
        if early_only:
            kv = rng.randint(1, N_VERSIONS // 10)
        else:
            kv = rng.choice(
                [rng.randint(1, N_VERSIONS // 10), rng.randint(1, latest_version)]
            )
        queries.append(Query(jur, day, kv))
    return queries


def _peak_rss_mib():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB.
    return rss / 2**20 if sys.platform == "darwin" else rss / 2**10


def _db_size_mib(path):
    total = 0
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = str(path) + suffix
        if os.path.exists(p):
            total += os.path.getsize(p)
    return total / 2**20


def test_full_scale(tmp_path):
    db = tmp_path / "rates.db"
    oracle = ReplayOracle()

    with VatRateStore.open(db) as store:
        for batch in generate_batches():
            store.publish(batch)
            oracle.publish(batch)
        assert store.latest_version == N_VERSIONS

    mixed = generate_queries(1, N_QUERIES, N_VERSIONS)
    early = generate_queries(2, N_QUERIES, N_VERSIONS, early_only=True)
    expected = oracle.query(mixed + early)

    # --- measured section: reopen + first batch of cross-version queries ---
    t0 = time.perf_counter()
    store = VatRateStore.open(db)
    actual = store.query(mixed)
    t_mixed = time.perf_counter() - t0
    assert actual == expected[:N_QUERIES]

    # early-version queries must meet the same budget
    t0 = time.perf_counter()
    actual_early = store.query(early)
    t_early = time.perf_counter() - t0
    assert actual_early == expected[N_QUERIES:]
    store.close()

    peak_mib = _peak_rss_mib()
    size_mib = _db_size_mib(db)

    print(f"\nreopen + first {N_QUERIES} mixed queries : {t_mixed:.3f} s")
    print(f"{N_QUERIES} early-version queries        : {t_early:.3f} s")
    print(f"peak RSS                        : {peak_mib:.1f} MiB")
    print(f"persisted size                  : {size_mib:.1f} MiB")

    assert t_mixed <= TIME_BUDGET_S
    assert t_early <= TIME_BUDGET_S
    assert peak_mib <= MEM_BUDGET_MIB
    assert size_mib <= SIZE_BUDGET_MIB
