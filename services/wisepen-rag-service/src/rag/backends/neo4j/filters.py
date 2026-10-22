"""把来源 metadata 条件编译为参数化 Cypher，不解释业务字段。"""

from math import isfinite

from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)


def compile_filters(filters: MetadataFilters) -> tuple[str, dict[str, object]]:
    """来源变量固定为 s；缺失属性不匹配，NOT 则按布尔取反处理。"""
    parameters: dict[str, object] = {}

    def compile_item(item: MetadataFilter | MetadataFilters) -> str:
        if isinstance(item, MetadataFilters):
            if not item.filters:
                raise ValueError("metadata filter groups must not be empty")

            children = [compile_item(child) for child in item.filters]

            if item.condition is FilterCondition.NOT:
                if len(children) != 1:
                    raise ValueError("NOT requires exactly one child")
                return f"NOT ({children[0]})"

            if item.condition not in (FilterCondition.AND, FilterCondition.OR):
                raise ValueError(f"unsupported filter condition: {item.condition}")
            # 用 AND / OR 连接子表达式
            return "(" + f" {item.condition.value.upper()} ".join(children) + ")"

        if not item.key or "." in item.key:
            raise ValueError("Neo4j metadata filters require a nonempty flat key")

        value = item.value

        if item.operator is FilterOperator.EQ:
            # EQ 仅接受字符串、布尔或有限数字
            if type(value) not in (str, int, float, bool) or (
                type(value) is float and not isfinite(value)
            ):
                raise ValueError("EQ requires a string, boolean or finite number")
            operator = "="

        elif item.operator is FilterOperator.IN:
            # IN 要求非空且同质的字符串或整数列表
            if (
                not isinstance(value, list)
                or not value
                or not (
                    all(type(entry) is str for entry in value)
                    or all(type(entry) is int for entry in value)
                )
            ):
                raise ValueError(
                    "IN requires a nonempty homogeneous string or integer list"
                )
            operator = "IN"

        elif item.operator in (
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
        ):
            # 范围比较仅接受有限数字
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError("range comparisons require a finite number")
            operator = {
                FilterOperator.GT: ">",
                FilterOperator.GTE: ">=",
                FilterOperator.LT: "<",
                FilterOperator.LTE: "<=",
            }[item.operator]

        else:
            raise ValueError(f"unsupported filter operator: {item.operator}")

        # 每个条件占两个参数位：键名与取值
        index = len(parameters) // 2
        parameters[f"key_{index}"] = f"meta_{item.key}"
        parameters[f"value_{index}"] = value
        # 缺失属性时 coalesce 兜底为 false
        return f"coalesce(s[$key_{index}] {operator} $value_{index}, false)"

    expression = compile_item(filters)
    return expression, parameters
