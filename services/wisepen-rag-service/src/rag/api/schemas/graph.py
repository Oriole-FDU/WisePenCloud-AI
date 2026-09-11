"""通用图谱检索 HTTP 输入与输出；垂类 metadata 过滤不在此传输边界暴露。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.retrieval.models import GraphSearchLevel, TraversalDirection

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SearchGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: NonEmptyText
    level: GraphSearchLevel = Field(default=GraphSearchLevel.HYBRID)
    seed_node_ids: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    resource_ids: list[NonEmptyText] | None = Field(default=None, max_length=20)
    node_categories: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    relation_types: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    direction: TraversalDirection = Field(default=TraversalDirection.BOTH)
    max_depth: int = Field(default=1, ge=0, le=3)
    vector_top_n: int = Field(default=20, ge=1, le=50)
    candidate_limit: int = Field(default=60, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=10)


class GraphHitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: str
    text: str
    score: float
    section_id: str | None
    section_path: list[str]


class SearchGraphResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    relevance_decision: str | None
    hits: list[GraphHitResponse]
