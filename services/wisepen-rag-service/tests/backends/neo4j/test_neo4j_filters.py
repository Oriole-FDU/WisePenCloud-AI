"""过滤值与键只进入参数；嵌套逻辑和不支持输入明确处理。"""

import pytest

from rag.backends.neo4j.filters import compile_filters
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)


@pytest.mark.parametrize(
    "operator, value, actual, expected",
    [
        (FilterOperator.EQ, "a", "a", True),
        (FilterOperator.IN, ["a", "b"], "b", True),
        (FilterOperator.GT, 3, 4, True),
        (FilterOperator.GTE, 3, 3, True),
        (FilterOperator.LT, 3, 4, False),
        (FilterOperator.LTE, 3, 3, True),
    ],
)
def test_comparisons(operator, value, actual, expected, filter_matches):
    expression, parameters = compile_filters(
        MetadataFilters([MetadataFilter("field", value, operator)])
    )
    assert filter_matches(expression, parameters, {"meta_field": actual}) is expected


def test_nested_filters_missing_property_negation_and_parameter_safety(filter_matches):
    key = "unsafe'] RETURN 1 //"
    value = "' OR true //"
    filters = MetadataFilters(
        [
            MetadataFilters(
                [MetadataFilter(key, value)], condition=FilterCondition.NOT
            ),
            MetadataFilters(
                [
                    MetadataFilter("resource_id", "a"),
                    MetadataFilter("resource_id", "b"),
                ],
                condition=FilterCondition.OR,
            ),
        ]
    )
    expression, parameters = compile_filters(filters)
    assert key not in expression and value not in expression
    assert filter_matches(expression, parameters, {"meta_resource_id": "b"})
    assert not filter_matches(expression, parameters, {"meta_resource_id": "c"})


@pytest.mark.parametrize(
    "filters",
    [
        MetadataFilters(),
        MetadataFilters(
            [MetadataFilter("a", 1), MetadataFilter("b", 1)],
            condition=FilterCondition.NOT,
        ),
        MetadataFilters([MetadataFilter("nested.key", "x")]),
        MetadataFilters([MetadataFilter("", "x")]),
        MetadataFilters([MetadataFilter("a", float("nan"))]),
        MetadataFilters([MetadataFilter("a", [], FilterOperator.IN)]),
        MetadataFilters([MetadataFilter("a", ["x", 1], FilterOperator.IN)]),
        MetadataFilters([MetadataFilter("a", True, FilterOperator.GT)]),
        MetadataFilters([MetadataFilter("a", "x", "unsupported")]),
        MetadataFilters([MetadataFilter("a", "x")], condition="unsupported"),
    ],
)
def test_unsupported_filter_shapes(filters):
    with pytest.raises(ValueError):
        compile_filters(filters)
