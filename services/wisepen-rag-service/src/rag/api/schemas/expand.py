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
from rag.application.rag.read.content import SectionContentView
from rag.domain.models.graph import KnowledgeRelationType
from rag.domain.models.section_navigation import SectionDirection

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


class SectionExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText
    section_id: NonEmptyText
    direction: SectionDirection
    char_budget: int = Field(default=12000, ge=1, le=50000)
    after_section_id: NonEmptyText | None = None


class SectionExpandResponse(BaseModel):
    from_section_id: str
    section_id: str
    title: str
    section_path: str
    text: str
    allowed_directions: list[SectionDirection]


class SectionChildrenExpandResponse(BaseModel):
    from_section_id: str
    sections: list[SectionContentView] = Field(default_factory=list)
    has_more: bool = False
    next_after_section_id: str | None = None
    budget_exhausted: bool = False
