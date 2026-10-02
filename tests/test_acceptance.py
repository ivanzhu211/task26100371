"""Acceptance scenario: three publishes with override and clear."""

from datetime import date

from vat_rates import Query, Revision, VatRateStore

DE = "DE"


def _build(store: VatRateStore) -> tuple[int, int, int]:
    v1 = store.publish([Revision(DE, date(2025, 1, 1), date(2025, 4, 1), 0.19)])
    v2 = store.publish([Revision(DE, date(2025, 2, 1), date(2025, 3, 1), 0.20)])
    v3 = store.publish([Revision(DE, date(2025, 2, 15), date(2025, 2, 20), None)])
    return v1, v2, v3


def test_version_numbers_are_strictly_increasing(tmp_path):
    with VatRateStore.open(tmp_path / "rates.db") as store:
        v1, v2, v3 = _build(store)
        assert (v1, v2, v3) == (1, 2, 3)
        assert store.latest_version == 3


def test_cleared_interval_hides_earlier_rate_at_v3(tmp_path):
    with VatRateStore.open(tmp_path / "rates.db") as store:
        _, v2, v3 = _build(store)
        # 2025-02-16 is inside the cleared interval [02-15, 02-20).
        res = store.query([Query(DE, date(2025, 2, 16), v3)])[0]
        assert res.rate is None and res.source_version == v3
        # Same day as known at v2: still 20% from version 2.
        res = store.query([Query(DE, date(2025, 2, 16), v2)])[0]
        assert res.rate == 0.20 and res.source_version == v2


def test_interval_boundaries_and_source_versions(tmp_path):
    with VatRateStore.open(tmp_path / "rates.db") as store:
        v1, _, v3 = _build(store)
        # 2025-02-20: clear interval ends (exclusive) -> 20% from v2.
        res = store.query([Query(DE, date(2025, 2, 20), v3)])[0]
        assert res.rate == 0.20 and res.source_version == 2
        # 2025-03-01: v2 interval ends (exclusive) -> 19% from v1.
        res = store.query([Query(DE, date(2025, 3, 1), v3)])[0]
        assert res.rate == 0.19 and res.source_version == v1
        # Day before any coverage: no applicable rate, no source version.
        res = store.query([Query(DE, date(2024, 12, 31), v3)])[0]
        assert res.rate is None and res.source_version is None
        # 2025-04-01: v1 interval ended (exclusive) -> no rate.
        res = store.query([Query(DE, date(2025, 4, 1), v3)])[0]
        assert res.rate is None and res.source_version is None


def test_results_survive_reopen(tmp_path):
    db = tmp_path / "rates.db"
    with VatRateStore.open(db) as store:
        _, v2, v3 = _build(store)
    with VatRateStore.open(db) as store:
        assert store.latest_version == v3
        res = store.query([Query(DE, date(2025, 2, 16), v3)])[0]
        assert res.rate is None and res.source_version == v3
        res = store.query([Query(DE, date(2025, 2, 16), v2)])[0]
        assert res.rate == 0.20 and res.source_version == v2
