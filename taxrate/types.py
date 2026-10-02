"""公共类型定义：修订与查询结果。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Revision:
    """一条区间修订。

    rate 为 None 表示清空该区间（无适用税率）；
    rate 为 0.0 是合法的零税率，与清空不同。
    日期为辖区当地纳税日，格式 'YYYY-MM-DD'，起日包含、失效日不包含；
    valid_to 为 None 表示无限未来。
    """

    jurisdiction: str
    valid_from: str
    valid_to: Optional[str]
    rate: Optional[float]


@dataclass(frozen=True)
class Resolution:
    """一次解析的结果。

    rate 为 None 表示该日期没有适用税率；
    否则 source_version 为实际提供该税率的发布版本。
    """

    rate: Optional[float]
    source_version: Optional[int]
