"""LOCATE endpoint 的请求与响应 schema。"""
from typing import Annotated

from common.utils.ranking import RankDecision
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.rag.navigate import RetrievalReadingBlockView
from rag.domain.models.graph import KnowledgeNode

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1)
]


class CandidateLocateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: NonEmptyText
    semantic_query: NonEmptyText
    lexical_query: NonEmptyText | None = None
    max_results: int = Field(default=10, ge=1, le=20)


class CandidateLocateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state_id: str
    retrieval_status: RankDecision
    nodes: list[KnowledgeNode]
    reading_blocks: list[RetrievalReadingBlockView]
