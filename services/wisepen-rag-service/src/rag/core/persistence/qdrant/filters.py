"""将通用 metadata 条件翻译成 Qdrant payload 条件，不丢弃不支持的输入。"""

from math import isfinite

from qdrant_client import models

from rag.core.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)


def compile_filters(filters: MetadataFilters) -> models.Filter:
    """只编译非空条件组；NOT 为单个子表达式的逻辑取反。"""
    if not filters.filters:
        raise ValueError("metadata filter groups must not be empty")
    conditions = [
        compile_filters(item)
        if isinstance(item, MetadataFilters)
        else _field_condition(item)
        for item in filters.filters
    ]
    if filters.condition is FilterCondition.AND:
        return models.Filter(must=conditions)
    if filters.condition is FilterCondition.OR:
        return models.Filter(should=conditions)
    if filters.condition is FilterCondition.NOT:
        if len(conditions) != 1:
            raise ValueError("NOT requires exactly one child")
        return models.Filter(must_not=conditions)
    raise ValueError(f"unsupported filter condition: {filters.condition}")


def _field_condition(item: MetadataFilter) -> models.FieldCondition:
    if not item.key:
        raise ValueError("metadata filter key must not be empty")
    key, value = f"metadata.{item.key}", item.value
    if item.operator is FilterOperator.EQ:
        if type(value) in (str, int, bool):
            return models.FieldCondition(key=key, match=models.MatchValue(value=value))
        if type(value) is float and isfinite(value):
            # MatchValue 不接受浮点数，数值等于用闭区间表达。
            return models.FieldCondition(
                key=key, range=models.Range(gte=value, lte=value)
            )
        raise ValueError("EQ requires a string, boolean or finite number")
    if item.operator is FilterOperator.IN:
        if (
            isinstance(value, list)
            and value
            and (
            all(type(entry) is str for entry in value)
            or all(type(entry) is int for entry in value)
        )
        ):
            return models.FieldCondition(key=key, match=models.MatchAny(any=value))
        raise ValueError("IN requires a nonempty homogeneous string or integer list")
    if item.operator in (
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
    ):
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError("range comparisons require a finite number")
        return models.FieldCondition(
            key=key, range=models.Range(**{item.operator.value: value})
        )
    raise ValueError(f"unsupported filter operator: {item.operator}")
