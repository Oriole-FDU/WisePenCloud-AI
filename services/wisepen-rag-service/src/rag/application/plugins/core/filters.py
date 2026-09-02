"""垂类图谱 metadata 过滤的声明式编译协议。"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from rag.domain.repositories.graph_node_vectors import (
    GraphFilterCondition,
    GraphFilterOperator,
)


@dataclass(frozen=True, slots=True)
class FilterOp:
    """声明一个面向调用方字段到图谱 metadata 标量字段的比较映射。"""

    target_field: str  # 图谱来源投影中由插件写入的 metadata 标量键。
    operator: GraphFilterOperator  # 该字段的底层比较方式。

    def __post_init__(self) -> None:
        if not self.target_field.isidentifier() or self.target_field.startswith("_"):
            raise ValueError("graph filter target field must be a public identifier")


def Eq(field: str) -> FilterOp:
    """声明字段与指定 metadata 键相等。"""
    return FilterOp(field, GraphFilterOperator.EQ)


def Gte(field: str) -> FilterOp:
    """声明字段为指定 metadata 键的包含下界。"""
    return FilterOp(field, GraphFilterOperator.GTE)


def Lte(field: str) -> FilterOp:
    """声明字段为指定 metadata 键的包含上界。"""
    return FilterOp(field, GraphFilterOperator.LTE)


class DeclarativeGraphFilter(BaseModel):
    """插件查询过滤基类，将 Annotated 声明编译为图谱检索 IR。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_conditions(self) -> tuple[GraphFilterCondition, ...]:
        """仅编译有值字段；每个公开字段必须声明一个 FilterOp。"""
        conditions: list[GraphFilterCondition] = []
        for field_name, field_info in type(self).model_fields.items():
            operations = [
                metadata
                for metadata in field_info.metadata
                if isinstance(metadata, FilterOp)
            ]
            if len(operations) != 1:
                raise ValueError(
                    f"declarative graph filter field requires exactly one FilterOp: {field_name}"
                )
            value = getattr(self, field_name)
            if value is None:
                continue
            operation = operations[0]
            conditions.append(
                GraphFilterCondition(
                    field=operation.target_field,
                    operator=operation.operator,
                    value=value,
                )
            )
        return tuple(conditions)
