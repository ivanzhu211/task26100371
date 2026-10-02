# 历史 VAT 税率解析库

由上层计税模块直接调用的 Python 库，数据持久化到 SQLite。
范围：各辖区标准 VAT 税率的发布与查询。

## 用法

```python
from taxrate import TaxRateLibrary, Revision

lib = TaxRateLibrary("rates.db")

# 批量发布修订；rate=None 表示清空该区间，valid_to=None 表示无限未来
v1 = lib.publish([Revision("DE", "2025-01-01", "2025-04-01", 0.19)])
v2 = lib.publish([Revision("DE", "2025-02-01", "2025-03-01", 0.20)])
v3 = lib.publish([Revision("DE", "2025-02-15", "2025-02-20", None)])

# 按辖区、交易日期、已知版本解析
lib.resolve("DE", "2025-02-16", v3)   # Resolution(rate=None, ...) 被清空
lib.resolve("DE", "2025-02-16", v2)   # Resolution(rate=0.20, source_version=2)

# 批量解析，允许混合辖区、日期与已知版本
lib.resolve_many([("DE", "2025-03-01", v3), ("FR", "2025-01-01", 1)])

lib.close()  # 正常关闭后重新打开，全部已发布版本仍可查
```

## 设计

- `revisions` 表：追加式发布审计日志，版本号全局严格递增。
- `segments` 表：物化时间线。发布时新区间覆盖当前时间线，被覆盖的
  旧段终结（记 `death` 版本），残余以原 `birth` 版本重新插入；清空只
  终结不插入。段行写入后不再修改，旧版本视图不受后续发布影响。
- `seg_rtree`：辖区散列 × 日期区间 × 版本区间的 R*Tree，支撑
  （辖区, 交易日期, 已知版本）点查。

## 测试

```
python3 -m pytest                # 全部（含完整规模性能测试，约 25 秒）
python3 -m pytest -m "not slow"  # 仅快速正确性测试
```
