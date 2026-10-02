"""验收场景一：三版本发布（设定 / 局部改税率 / 局部清空）。"""
import pytest

from taxrate import Revision, TaxRateLibrary


@pytest.fixture()
def lib(tmp_path):
    library = TaxRateLibrary(str(tmp_path / "rates.db"))
    yield library
    library.close()


def test_three_version_scenario(lib):
    v1 = lib.publish([Revision("DE", "2025-01-01", "2025-04-01", 0.19)])
    v2 = lib.publish([Revision("DE", "2025-02-01", "2025-03-01", 0.20)])
    v3 = lib.publish([Revision("DE", "2025-02-15", "2025-02-20", None)])
    assert (v1, v2, v3) == (1, 2, 3)

    # 第三版清空区间内：无适用税率
    r = lib.resolve("DE", "2025-02-16", v3)
    assert r.rate is None and r.source_version is None
    # 同一日期按第二版查询：仍为 20%，来源版本 2
    r = lib.resolve("DE", "2025-02-16", v2)
    assert r.rate == pytest.approx(0.20) and r.source_version == v2
    # 清空区间右端点（失效日不包含）：恢复为 20%
    r = lib.resolve("DE", "2025-02-20", v3)
    assert r.rate == pytest.approx(0.20) and r.source_version == v2
    # 3 月 1 日回到第一版的 19%
    r = lib.resolve("DE", "2025-03-01", v3)
    assert r.rate == pytest.approx(0.19) and r.source_version == v1
    # 局部覆盖不吞掉旧区间两侧
    r = lib.resolve("DE", "2025-01-15", v3)
    assert r.rate == pytest.approx(0.19) and r.source_version == v1
    # 区间之外（4 月 1 日起）无适用税率
    r = lib.resolve("DE", "2025-04-01", v3)
    assert r.rate is None and r.source_version is None


def test_old_version_unaffected_by_later_publications(lib):
    lib.publish([Revision("DE", "2025-01-01", "2025-04-01", 0.19)])
    lib.publish([Revision("DE", "2025-01-01", "2025-04-01", None)])
    r = lib.resolve("DE", "2025-02-01", 1)
    assert r.rate == pytest.approx(0.19) and r.source_version == 1


def test_zero_rate_is_distinct_from_no_rate(lib):
    lib.publish([Revision("DE", "2025-01-01", None, 0.0)])
    r = lib.resolve("DE", "2025-06-01", 1)
    assert r.rate == 0.0 and r.source_version == 1
    r = lib.resolve("FR", "2025-06-01", 1)
    assert r.rate is None and r.source_version is None
