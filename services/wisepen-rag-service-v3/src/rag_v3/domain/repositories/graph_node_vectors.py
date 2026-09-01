"""节点图谱向量投影端口。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from rag_v3.application.graph.models import (
    GraphNodeProjection,
)
from rag_v3.domain.acl import PermissionScope, ResourceAcl


class GraphFilterOperator(StrEnum):
    EQ = "eq"
    GTE = "gte"
    LTE = "lte"


class GraphFilterCondition(BaseModel):
    """插件编译出的图谱索引过滤条件，仅在一次查询中传递。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    operator: GraphFilterOperator
    value: str | int | float | bool

    @model_validator(mode="after")
    def _require_property_safe_field(self) -> "GraphFilterCondition":
        if not self.field.isidentifier() or self.field.startswith("_"):
            raise ValueError("graph filter field must be a public identifier")
        return self


@dataclass(frozen=True, slots=True)
class GraphVectorCandidate:
    """图谱 Qdrant 初检返回的来源投影引用，不是图谱检索结果。"""

    projection_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    rank: int
    branch: str


class GraphNodeVectorRepository(Protocol):
    """管理节点 Dense 图谱索引，不负责查询或分数融合。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        node_categories: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...
