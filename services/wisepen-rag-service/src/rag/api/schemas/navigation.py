from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.rag.graph_extraction import KnowledgeRelationType
from rag.application.rag.knowledge_navigation import KnowledgeNavigationDirection

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class NavigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: NonEmptyText


class LocateRequest(NavigationRequest):
    semantic_query: NonEmptyText
    lexical_query: NonEmptyText | None = None
    max_results: int = Field(default=10, ge=1, le=20)


class CypherRequest(NavigationRequest):
    state_id: NonEmptyText
    node_ids: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=16)
    query: NonEmptyText | None = None
    relation_types: tuple[KnowledgeRelationType, ...] = Field(
        default=(),
        max_length=16,
    )
    direction: KnowledgeNavigationDirection = KnowledgeNavigationDirection.BOTH
    max_depth: int = Field(default=1, ge=1, le=2)
    max_results: int = Field(default=10, ge=1, le=20)


class ReadSectionsRequest(NavigationRequest):
    state_id: NonEmptyText
    section_ids: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=12)
