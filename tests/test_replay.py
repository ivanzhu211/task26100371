"""验收场景二：与独立逐条回放的参照实现对比，重开库后结论不变。"""
import random

import pytest

from taxrate import Revision, TaxRateLibrary


class Replayer:
    """参照实现：保存原始发布记录，查询时逐条回放。

    在不晚于 known_version 的修订中，取覆盖交易日期的最高版本修订；
    该修订为清空则无适用税率。
    """

    def __init__(self):
        self.log = []  # (version, Revision)

    def publish(self, version, revisions):
        for rev in revisions:
            self.log.append((version, rev))

    def resolve(self, jurisdiction, date, known_version):
        best = None
        for version, rev in self.log:
            if version > known_version or rev.jurisdiction != jurisdiction:
                continue
            if rev.valid_from > date:
                continue
            if rev.valid_to is not None and rev.valid_to <= date:
                continue
            if best is None or version > best[0]:
                best = (version, rev)
        if best is None or best[1].rate is None:
            return (None, None)
        return (best[1].rate, best[0])


def _random_date(rng, start_ordinal, end_ordinal):
    from datetime import date, timedelta

    ordinal = rng.randint(start_ordinal, end_ordinal)
    return (date(2020, 1, 1) + timedelta(days=ordinal)).isoformat()


def _make_sequence(rng, n_versions=60, max_batch=4):
    """生成含追溯、未来生效、嵌套、清空、零税率恢复的发布序列。"""
    jurisdictions = ["DE", "FR", "NL"]
    sequence = []
    for _ in range(n_versions):
        batch = []
        # 同一批内同一辖区只放一条修订，满足区间互不重叠的前置保证
        for jurisdiction in rng.sample(
            jurisdictions, rng.randint(1, min(max_batch, len(jurisdictions)))
        ):
            start = _random_date(rng, 0, 2000)
            if rng.random() < 0.2:
                end = None  # 无限未来
            else:
                end = _random_date(rng, 0, 2000)
                if end <= start:
                    end = _random_date(
                        rng,
                        min(2000, 1 + int(start[5:7]) * 30),
                        2000,
                    )
                    if end <= start:
                        start, end = "2020-01-01", "2020-06-01"
            roll = rng.random()
            if roll < 0.2:
                rate = None  # 清空
            elif roll < 0.35:
                rate = 0.0  # 零税率（含清空后恢复为零税率的情况）
            else:
                rate = round(rng.uniform(0.01, 0.30), 4)
            batch.append(Revision(jurisdiction, start, end, rate))
        sequence.append(batch)
    return sequence


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_matches_independent_replay(tmp_path, seed):
    rng = random.Random(seed)
    sequence = _make_sequence(rng)
    db_path = str(tmp_path / "rates.db")

    replayer = Replayer()
    lib = TaxRateLibrary(db_path)
    max_version = 0
    for batch in sequence:
        version = lib.publish(batch)
        assert version == max_version + 1  # 全局严格递增
        replayer.publish(version, batch)
        max_version = version

    queries = []
    for _ in range(3000):
        queries.append(
            (
                rng.choice(["DE", "FR", "NL", "XX"]),  # XX 从未发布
                _random_date(rng, 0, 2200),  # 含未覆盖日期
                rng.randint(0, max_version),  # 含早于任何发布的版本 0
            )
        )

    def check_all(library):
        results = library.resolve_many(queries)
        assert len(results) == len(queries)
        for (jurisdiction, date, known_version), res in zip(queries, results):
            exp_rate, exp_version = replayer.resolve(
                jurisdiction, date, known_version
            )
            assert res.rate == exp_rate, (jurisdiction, date, known_version)
            assert res.source_version == exp_version, (
                jurisdiction,
                date,
                known_version,
            )

    check_all(lib)
    lib.close()

    # 重新打开后结论不变
    lib2 = TaxRateLibrary(db_path)
    check_all(lib2)
    lib2.close()
