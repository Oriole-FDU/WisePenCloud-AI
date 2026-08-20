"""EXPAND endpoint 的请求与响应 schema。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.rag.navigate import (
    DiscoveredKnowledgeNodeView,
    GraphNodeView,
    GraphPathView,
    GraphReadingBlockView,
    TraversalDirection,
)
from rag.domain.models.graph import KnowledgeRelationType

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class GraphExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: NonEmptyText
    state_id: NonEmptyText
    seed_node_ids: list[NonEmptyText] = Field(min_length=1, max_length=16)
    query: NonEmptyText
    relation_types: list[KnowledgeRelationType] = Field(
        default_factory=list,
        max_length=16,
    )
    direction: TraversalDirection = TraversalDirection.BOTH
    max_depth: int = Field(default=1, ge=1, le=2)
    max_results: int = Field(default=10, ge=1, le=20)


class GraphExpandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state_id: str
    traversal_direction: TraversalDirection
    seed_nodes: list[GraphNodeView]
    discovered_nodes: list[DiscoveredKnowledgeNodeView]
    paths: list[GraphPathView]
    evidence_reading_blocks: list[GraphReadingBlockView]


