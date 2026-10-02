"""历史税率解析库入口：发布与查询。

实现思路：除追加式的修订审计表外，按辖区维护一张物化时间线
（segments 表）。每次发布把新区间覆盖到当前时间线上：被覆盖的旧段
终结（记录 death 版本），其左右残余以原 birth 版本重新插入；
清空修订只终结不插入。段行一旦写入，其覆盖区间不再修改，因此
旧版本视图不受后续发布影响。任意版本 v 下存活的段互不相交。

查询走 seg_rtree（辖区散列 × 日期区间 × 版本区间的 R*Tree），
定位 (birth<=v<death) 且覆盖交易日期的唯一段；发布路径按辖区缓存
当前存活段（有序列表 + 二分定位），只向库中写入增量。
"""
from __future__ import annotations

import sqlite3
from bisect import bisect_right
from typing import Dict, Iterable, List, Optional, Tuple

from . import store
from .types import Resolution, Revision

_RESOLVE_SQL = """
SELECT rate, birth FROM seg_rtree
WHERE jkey0 = ? AND jkey1 = ? AND day0 <= ? AND day1 >= ?
  AND ver0 <= ? AND ver1 >= ? AND jur = ?
LIMIT 1
"""

_ALIVE_SQL = """
SELECT rowid, valid_from, valid_to, rate, birth FROM segments
WHERE jurisdiction = ? AND death IS NULL
ORDER BY valid_from
"""

_KILL_SEGMENT_SQL = "UPDATE segments SET death = ? WHERE rowid = ?"
_KILL_RTREE_SQL = "UPDATE seg_rtree SET ver1 = ? WHERE id = ?"

_INSERT_SEGMENT_SQL = (
    "INSERT INTO segments "
    "(jurisdiction, valid_from, valid_to, rate, birth, death) "
    "VALUES (?, ?, ?, ?, ?, NULL)"
)
_INSERT_RTREE_SQL = (
    "INSERT INTO seg_rtree "
    "(id, jkey0, jkey1, day0, day1, ver0, ver1, rate, birth, jur) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_AUDIT_SQL = (
    "INSERT INTO revisions "
    "(version, jurisdiction, valid_from, valid_to, rate, is_clear) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

# 段记录：[valid_from, valid_to, rate, birth, rowid]；valid_to 为 None 表示无限未来
_Segment = List[object]


class _Timeline:
    """某辖区当前存活段的有序列表（按 valid_from 升序，互不相交）。"""

    __slots__ = ("starts", "segs")

    def __init__(self, segs: List[_Segment]) -> None:
        self.segs = segs
        self.starts = [s[0] for s in segs]

    def splice(self, lo: int, hi: int, pieces: List[_Segment]) -> None:
        self.segs[lo:hi] = pieces
        self.starts[lo:hi] = [p[0] for p in pieces]


class TaxRateLibrary:
    """面向上层计税模块的 API。

    调用串行执行；close 后重新打开，全部已发布版本仍可查。
    """

    def __init__(self, db_path: str) -> None:
        self._conn: sqlite3.Connection = store.open_db(db_path)
        self._timelines: Dict[str, _Timeline] = {}

    # ---- 发布 ----

    def publish(self, revisions: Iterable[Revision]) -> int:
        """批量发布修订，返回本次发布的全局版本号。"""
        batch = list(revisions)
        with self._conn:  # 事务：整批要么全部生效，要么全部回滚
            version = store.next_version(self._conn)
            self._conn.executemany(
                _AUDIT_SQL,
                [
                    (
                        version,
                        rev.jurisdiction,
                        rev.valid_from,
                        rev.valid_to,
                        rev.rate,
                        1 if rev.rate is None else 0,
                    )
                    for rev in batch
                ],
            )
            for rev in batch:
                self._apply(rev, version)
        return version

    def _timeline(self, jurisdiction: str) -> _Timeline:
        timeline = self._timelines.get(jurisdiction)
        if timeline is None:
            rows = self._conn.execute(_ALIVE_SQL, (jurisdiction,)).fetchall()
            timeline = _Timeline(
                [[r[1], r[2], r[3], r[4], r[0]] for r in rows]
            )
            self._timelines[jurisdiction] = timeline
        return timeline

    def _insert_segment(
        self,
        jurisdiction: str,
        jkey: int,
        valid_from: str,
        valid_to: Optional[str],
        rate: float,
        birth: int,
    ) -> int:
        cur = self._conn.execute(
            _INSERT_SEGMENT_SQL,
            (jurisdiction, valid_from, valid_to, rate, birth),
        )
        rowid = int(cur.lastrowid)
        day0 = store.day_ordinal(valid_from)
        day1 = (
            store.day_ordinal(valid_to) - 1
            if valid_to is not None
            else store.INF_DAY
        )
        self._conn.execute(
            _INSERT_RTREE_SQL,
            (rowid, jkey, jkey, day0, day1, birth, store.INF_VERSION,
             rate, birth, jurisdiction),
        )
        return rowid

    def _apply(self, rev: Revision, version: int) -> None:
        jurisdiction = rev.jurisdiction
        jkey = store.jurisdiction_key(jurisdiction)
        f, t = rev.valid_from, rev.valid_to
        timeline = self._timeline(jurisdiction)
        starts, segs = timeline.starts, timeline.segs

        # 候选起点：最后一个 valid_from <= f 的段；其后的段起点都 > f
        i = bisect_right(starts, f) - 1
        if i < 0:
            i = 0
        # 重叠范围 [lo, hi)：valid_to > f 且 valid_from < t
        lo = i
        if i < len(segs) and segs[i][1] is not None and segs[i][1] <= f:
            lo = i + 1  # 段 i 在 f 之前结束，不重叠
        hi = lo
        while hi < len(segs) and (t is None or starts[hi] < t):
            hi += 1

        pieces: List[_Segment] = []
        for k in range(lo, hi):
            seg_from, seg_to, seg_rate, seg_birth, seg_rowid = segs[k]
            self._conn.execute(_KILL_SEGMENT_SQL, (version, seg_rowid))
            self._conn.execute(_KILL_RTREE_SQL, (version - 1, seg_rowid))
            if seg_from < f:
                pieces.append([seg_from, f, seg_rate, seg_birth])
            if t is not None and (seg_to is None or seg_to > t):
                pieces.append([t, seg_to, seg_rate, seg_birth])
        if rev.rate is not None and (t is None or t > f):
            pieces.append([f, t, rev.rate, version])
        pieces.sort(key=lambda p: p[0])

        for p in pieces:
            rowid = self._insert_segment(
                jurisdiction, jkey, p[0], p[1], p[2], p[3]
            )
            p.append(rowid)
        timeline.splice(lo, hi, pieces)

    # ---- 查询 ----

    def resolve(
        self, jurisdiction: str, date: str, known_version: int
    ) -> Resolution:
        """按辖区、交易日期、已知版本解析税率。"""
        jkey = store.jurisdiction_key(jurisdiction)
        day = store.day_ordinal(date)
        row = self._conn.execute(
            _RESOLVE_SQL,
            (jkey, jkey, day, day, known_version, known_version, jurisdiction),
        ).fetchone()
        if row is None:
            return Resolution(rate=None, source_version=None)
        return Resolution(rate=float(row[0]), source_version=int(row[1]))

    def resolve_many(
        self, queries: Iterable[Tuple[str, str, int]]
    ) -> List[Resolution]:
        """批量解析，允许混合辖区、日期与已知版本。"""
        return [self.resolve(j, d, v) for j, d, v in queries]

    def close(self) -> None:
        """正常关闭，保证已发布内容持久化。"""
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> "TaxRateLibrary":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
