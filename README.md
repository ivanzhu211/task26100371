# vat_rates — 历史 VAT 税率解析库

由上层计税模块直接调用的 Python 库，基于 SQLite 持久化。范围仅覆盖各辖区
标准 VAT 税率的**发布**与**版本化查询**。

## 核心概念

- **修订（Revision）**：`(辖区, 生效起日, 失效日, 新税率 | 清空)`。
  区间为左闭右开 `[start, end)`；`end=None` 表示无限未来；`rate=None`
  表示清空该区间（无适用税率）。零税率 `0.0` 是合法税率，与清空不同。
  日期由上游换算为辖区当地纳税日，本库不做时区处理。
- **发布（publish）**：一批修订原子提交，获得全局唯一、严格递增的版本号；
  已发布内容不可修改。同一批内同一辖区的区间须互不重叠（由上游保证，
  库内校验起止日合法性）。
- **查询（query）**：`(辖区, 交易日, 已知版本)`。只考虑不晚于已知版本的
  发布，在覆盖交易日的修订中取发布版本最高者：若该修订为清空则无适用
  税率（不会露出更早税率），否则返回其税率。区间之外保留原有结果；
  从未被任何修订覆盖的日期返回无适用税率。
- **结果（QueryResult）**：`rate`（无适用税率时为 `None`）与
  `source_version`（决定该结果的发布版本；完全未覆盖时为 `None`——
  清空决定的"无税率"会返回清空所在版本号）。

## 用法

```python
from datetime import date
from vat_rates import VatRateStore, Revision, Query

with VatRateStore.open("rates.db") as store:
    v1 = store.publish([Revision("DE", date(2025, 1, 1), date(2025, 4, 1), 0.19)])
    v2 = store.publish([Revision("DE", date(2025, 2, 1), date(2025, 3, 1), 0.20)])
    v3 = store.publish([Revision("DE", date(2025, 2, 15), date(2025, 2, 20), None)])

    store.query([Query("DE", date(2025, 2, 16), v3)])  # -> rate=None (v3 清空)
    store.query([Query("DE", date(2025, 2, 16), v2)])  # -> rate=0.20, source=v2
    store.query([Query("DE", date(2025, 3, 1), v3)])   # -> rate=0.19, source=v1
```

`publish` / `query` 均接受批量参数；同一批查询可混合不同辖区、日期与
已知版本，无需按版本排序。正常关闭后重新打开，全部已发布版本仍可查询。

## 实现要点

- 单表 `revisions(version, jurisdiction, start, end, rate)`，版本号存于
  `meta` 表，发布在单事务内完成（追加写，已发布行不可变）。
- 查询为单条 SQL：覆盖索引 `(jurisdiction, start, version, end, rate)`
  上一次扫描，利用 SQLite 的 bare-column-with-`MAX(version)` 语义直接
  取到最高版本行的税率，无排序、无回表。
- 清空与税率统一存储（`rate IS NULL` 即清空），因此"清空遮蔽更早税率、
  后续发布可再覆盖"由同一条最高版本规则自然成立。

## 运行测试

```bash
python3 -m pytest tests/ -q -s
```

- `tests/test_acceptance.py` — 验收场景一（19% → 20% → 清空，边界与来源版本）
- `tests/test_replay.py` — 追溯/未来生效/嵌套/清空/零税率恢复序列，
  与独立逐条回放 oracle（`tests/oracle.py`）全量比对，并重开库复验
- `tests/test_perf.py` — 完整规模：50 辖区、2 万版本、20 万条修订
  （JUR-00 独占 10 万条），混合早晚版本与未覆盖日期的 2000+2000 条查询

## 实测结果（本机，Python 3.14 / macOS）

| 指标 | 预算 | 实测 |
| --- | --- | --- |
| 重开库 + 首批 2000 条跨版本查询 | ≤ 3 s | 0.244 s |
| 2000 条早期版本（≤ v2000）查询 | ≤ 3 s | 0.233 s |
| 进程峰值内存 | ≤ 256 MiB | 75 MiB |
| 持久化体积（20 万条修订） | ≤ 512 MiB | 18.8 MiB |

正确性：全部查询结果与独立回放 oracle 一致，重开库后结论不变。
