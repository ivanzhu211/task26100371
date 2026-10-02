"""验收场景三：完整规模 + 倾斜分布下的正确性、耗时、内存与体积。

规模：50 个辖区、20,000 个发布版本、累计 200,000 条区间修订，
其中一个辖区独占 100,000 条；修订区间既有大量交叠也有互不覆盖。
重新打开库后首批 2000 条跨版本混合查询（含未覆盖日期）须在 3 秒内
完成（计时含打开库），峰值内存 <= 256 MiB，持久化数据 <= 512 MiB。
"""
import os
import resource
import sys
import time
from datetime import date, timedelta

import pytest

from taxrate import Revision, TaxRateLibrary
from tests.test_replay import Replayer

N_JURISDICTIONS = 50
N_VERSIONS = 20_000
HOT_REVISIONS = 100_000  # 单一辖区独占 10 万条
TOTAL_REVISIONS = 200_000
N_QUERIES = 2_000
QUERY_BUDGET_SECONDS = 3.0
MEMORY_BUDGET_BYTES = 256 * 1024 * 1024
STORAGE_BUDGET_BYTES = 512 * 1024 * 1024

HOT = "JUR-HOT"
DAY0 = date(2015, 1, 1)
SPAN_DAYS = 3650  # 10 年


def _iso(day_offset):
    return (DAY0 + timedelta(days=day_offset)).isoformat()


class _Random:
    """轻量确定性随机源，避免测试数据在运行间变化。"""

    def __init__(self, seed):
        self._state = seed

    def next(self):
        self._state = (
            self._state * 6364136223846793005 + 1442695040888963407
        ) & ((1 << 64) - 1)
        return self._state >> 11

    def randint(self, low, high):  # 含两端
        return low + self.next() % (high - low + 1)


def _generate(rng):
    """生成 (版本 -> 修订列表)。同批同辖区区间互不重叠。"""
    jurisdictions = [HOT] + [f"JUR-{i:03d}" for i in range(N_JURISDICTIONS - 1)]
    hot_per_version = HOT_REVISIONS // N_VERSIONS  # 5
    other_total = TOTAL_REVISIONS - HOT_REVISIONS
    other_per_version = other_total // N_VERSIONS  # 5
    sequence = []
    for _ in range(N_VERSIONS):
        batch = []
        # 热点辖区：每版 5 条，各自落在独立槽位内（批内不重叠，
        # 跨版本槽位随机偏移，产生大量交叠）
        slot = SPAN_DAYS // hot_per_version
        for s in range(hot_per_version):
            start = s * slot + rng.randint(0, slot - 2)
            length = rng.randint(1, slot)
            # 钳制在本槽位内，保证同批同辖区区间互不重叠
            end = min(start + length, (s + 1) * slot)
            roll = rng.randint(1, 100)
            if roll <= 10:
                rate = None  # 清空
            elif roll <= 15:
                rate = 0.0  # 零税率
            else:
                rate = rng.randint(1, 3000) / 10000.0
            batch.append(
                Revision(
                    HOT,
                    _iso(start),
                    None
                    if roll % 7 == 0 and s == hot_per_version - 1
                    else _iso(end),
                    rate,
                )
            )
        # 其余辖区：每版共 5 条，随机落到不同辖区
        chosen = sorted(
            {rng.randint(1, N_JURISDICTIONS - 1) for _ in range(other_per_version)}
        )
        for idx in chosen:
            start = rng.randint(0, SPAN_DAYS - 2)
            end = start + rng.randint(1, 900)
            roll = rng.randint(1, 100)
            if roll <= 10:
                rate = None
            elif roll <= 15:
                rate = 0.0
            else:
                rate = rng.randint(1, 3000) / 10000.0
            batch.append(
                Revision(
                    jurisdictions[idx],
                    _iso(start),
                    None if roll % 7 == 0 else _iso(min(end, SPAN_DAYS)),
                    rate,
                )
            )
        sequence.append(batch)
    return sequence


def _db_size(path):
    total = 0
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            total += os.path.getsize(path + suffix)
    return total


@pytest.mark.slow
def test_full_scale(tmp_path):
    rng = _Random(20261003)
    sequence = _generate(rng)
    assert sum(len(b) for b in sequence) <= TOTAL_REVISIONS
    db_path = str(tmp_path / "rates.db")

    lib = TaxRateLibrary(db_path)
    replayer = Replayer()
    t0 = time.perf_counter()
    for batch in sequence:
        version = lib.publish(batch)
        replayer.publish(version, batch)
    publish_seconds = time.perf_counter() - t0
    lib.close()

    # 混合查询：热点 + 普通辖区，早期 + 近期版本，含未覆盖日期
    queries = []
    for _ in range(N_QUERIES):
        if rng.randint(1, 100) <= 60:
            jurisdiction = HOT
        else:
            jurisdiction = f"JUR-{rng.randint(0, N_JURISDICTIONS - 2):03d}"
        day = rng.randint(0, SPAN_DAYS + 400)  # 部分落在所有区间之外
        roll = rng.randint(1, 100)
        if roll <= 30:
            known_version = rng.randint(1, N_VERSIONS // 10)  # 早期版本
        else:
            known_version = rng.randint(1, N_VERSIONS)
        queries.append((jurisdiction, _iso(day), known_version))

    # 计时：包含重新打开库的准备工作
    t0 = time.perf_counter()
    lib = TaxRateLibrary(db_path)
    results = lib.resolve_many(queries)
    query_seconds = time.perf_counter() - t0

    # 抽样与独立回放比对（含早期版本与未覆盖日期）
    for i in range(0, N_QUERIES, max(1, N_QUERIES // 120)):
        jurisdiction, day, known_version = queries[i]
        exp_rate, exp_version = replayer.resolve(jurisdiction, day, known_version)
        assert results[i].rate == exp_rate, queries[i]
        assert results[i].source_version == exp_version, queries[i]

    lib.close()

    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":  # Linux 单位为 KiB
        maxrss *= 1024
    storage = _db_size(db_path)

    total_revisions = sum(len(b) for b in sequence)
    print(f"\n[perf] 发布 {N_VERSIONS} 版 / {total_revisions} 条修订，"
          f"耗时 {publish_seconds:.2f}s")
    print(f"[perf] 重开库 + {N_QUERIES} 条混合查询耗时 {query_seconds:.3f}s")
    print(f"[perf] 进程峰值内存 {maxrss / 1024 / 1024:.1f} MiB")
    print(f"[perf] 持久化体积 {storage / 1024 / 1024:.1f} MiB")

    assert query_seconds < QUERY_BUDGET_SECONDS
    assert maxrss < MEMORY_BUDGET_BYTES
    assert storage < STORAGE_BUDGET_BYTES
