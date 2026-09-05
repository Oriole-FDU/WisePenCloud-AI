"""各类检索投影共用的 metadata 过滤条件契约。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class MetadataFilterOperator(StrEnum):
    EQ = "eq"
    GTE = "gte"
    LTE = "lte"


class MetadataFilterCondition(BaseModel):
    """插件编译出的索引过滤条件，仅在一次查询中传递。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    operator: MetadataFilterOperator
    value: str | int | float | bool

    @model_validator(mode="after")
    def _require_property_safe_field(self) -> "MetadataFilterCondition":
        if not self.field.isidentifier() or self.field.startswith("_"):
            raise ValueError("metadata filter field must be a public identifier")
        return self
