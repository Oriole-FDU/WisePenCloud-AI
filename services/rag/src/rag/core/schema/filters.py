"""存储共享的 metadata 条件表达式，不解释 ACL、发布状态或插件语义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FilterOperator(StrEnum):
    EQ = "eq"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class FilterCondition(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    """单项字段条件；key 是 metadata 内的相对路径。"""

    key: str
    value: str | int | float | bool | list[str] | list[int]
    operator: FilterOperator = FilterOperator.EQ


@dataclass(frozen=True, slots=True)
class MetadataFilters:
    """递归条件组；非空，NOT 恰好一个子条件，由后端转换边界核对。"""

    filters: list[MetadataFilter | MetadataFilters] = field(default_factory=list)
    condition: FilterCondition = FilterCondition.AND
