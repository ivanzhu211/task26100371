"""SQLite 持久化层：schema 与连接管理。"""
from __future__ import annotations

import sqlite3
import zlib
from datetime import date

#: 无限未来对应的日期序数上界（9999-12-31）
INF_DAY = date(9999, 12, 31).toordinal()
#: 未终结段的版本上界。R*Tree 坐标为 32 位浮点，取值须小于 2**24
#: 才能精确表示；该上界（约 1677 万）远高于规格要求的 2 万版本。
INF_VERSION = (1 << 24) - 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    version INTEGER NOT NULL,
    jurisdiction TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    rate REAL,
    is_clear INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    jurisdiction TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    rate REAL NOT NULL,
    birth INTEGER NOT NULL,
    death INTEGER
);
CREATE INDEX IF NOT EXISTS idx_segments_alive
    ON segments (jurisdiction, valid_from) WHERE death IS NULL;
CREATE VIRTUAL TABLE IF NOT EXISTS seg_rtree USING rtree(
    id,
    jkey0, jkey1,
    day0, day1,
    ver0, ver1,
    +rate REAL,
    +birth INTEGER,
    +jur TEXT
);
"""


def open_db(path: str) -> sqlite3.Connection:
    """打开（必要时创建）数据库并确保 schema 存在。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('next_version', 1)"
    )
    conn.commit()
    return conn


def next_version(conn: sqlite3.Connection) -> int:
    """分配下一个全局严格递增的版本号（须在事务内调用）。"""
    row = conn.execute(
        "UPDATE meta SET value = value + 1 WHERE key = 'next_version' "
        "RETURNING value"
    ).fetchone()
    assert row is not None
    return int(row[0]) - 1


def jurisdiction_key(jurisdiction: str) -> int:
    """辖区字符串的散列键（取模进 float32 精确范围，碰撞由精确匹配兜底）。"""
    return zlib.crc32(jurisdiction.encode("utf-8")) % 100003


def day_ordinal(iso_date: str) -> int:
    """'YYYY-MM-DD' 转为日期序数。"""
    return date.fromisoformat(iso_date).toordinal()
