from __future__ import annotations

from dataclasses import dataclass

import pytest

from rag.application.rag.retrieval import (
    RagCandidateRetriever,
    RagPermissionScope,
    RagRetrievalCandidate,
    RagRetrievalRequest,
)
from common.utils.ranking import RankingPipeline, ScoreSignal, ScoreSignalKind
from common.utils.ranking.fusion import WeightedRrfFusion
from common.core.domain import GroupRoleType


@dataclass(frozen=True, slots=True)
class _EmbeddingResult:
    embeddings: list[list[float]]


class _EmbeddingClient:
    async def aembed(self, input) -> _EmbeddingResult:
        return _EmbeddingResult(embeddings=[[0.1, 0.2]])


class _CandidateRepository:
    def __init__(self, candidates: tuple[RagRetrievalCandidate, ...]) -> None:
        self.candidates = candidates
        self.requests = []

    async def retrieve_candidates(self, request):
        self.requests.append(request)
        return self.candidates


class _ProjectionRepository:
    def __init__(self, applied_revisions: dict[str, str]) -> None:
        self.applied_revisions = applied_revisions
        self.requested_resource_ids = []

    async def get_applied_revisions(self, resource_ids):
        self.requested_resource_ids.append(tuple(resource_ids))
        return self.applied_revisions


class _PermissionAuthorizer:
    def __init__(self, denied_resource_ids: tuple[str, ...] = ()) -> None:
        self.denied_resource_ids = frozenset(denied_resource_ids)

    async def accessible_resource_ids(self, resource_ids, scope):
        return frozenset(resource_ids) - self.denied_resource_ids


def _candidate(
    *,
    chunk_id: str,
    resource_id: str,
    revision: str,
    rank: int,
) -> RagRetrievalCandidate:
    return RagRetrievalCandidate(
        chunk_id=chunk_id,
        reading_block_id=f"block-{chunk_id}",
        section_id=f"section-{chunk_id}",
        section_path=("标题",),
        resource_id=resource_id,
        content_revision=revision,
        raw_text=f"正文 {chunk_id}",
        anchor_labels=(),
        source_ref_id=f"ref-{chunk_id}",
        signals=(
            ScoreSignal(
                candidate_id=chunk_id,
                name="qdrant_dense",
                value=0.9,
                kind=ScoreSignalKind.VECTOR,
                rank=rank,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_retriever_drops_non_applied_revisions_before_ranking() -> None:
    current = _candidate(
        chunk_id="current",
        resource_id="resource-1",
        revision="revision-2",
        rank=2,
    )
    staged = _candidate(
        chunk_id="staged",
        resource_id="resource-2",
        revision="revision-3",
        rank=1,
    )
    projection_repository = _ProjectionRepository(
        {"resource-1": "revision-2", "resource-2": "revision-2"}
    )
    candidate_repository = _CandidateRepository((staged, current))
    retriever = RagCandidateRetriever(
        embedding_client=_EmbeddingClient(),
        candidate_repository=candidate_repository,
        projection_repository=projection_repository,
        permission_authorizer=_PermissionAuthorizer(),
        ranking_pipeline=RankingPipeline(fusion=WeightedRrfFusion()),
    )

    hits = await retriever.retrieve(
        RagRetrievalRequest(
            query=" 查询 ",
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={"group-1": GroupRoleType.MEMBER},
            ),
            resource_ids=("resource-1", "resource-2"),
        )
    )

    assert [hit.chunk_id for hit in hits] == ["current"]
    assert projection_repository.requested_resource_ids == [
        ("resource-2", "resource-1")
    ]
    assert candidate_repository.requests[0].query_text == "查询"


@pytest.mark.asyncio
async def test_retriever_returns_empty_when_no_candidate_is_applied() -> None:
    retriever = RagCandidateRetriever(
        embedding_client=_EmbeddingClient(),
        candidate_repository=_CandidateRepository(
            (
                _candidate(
                    chunk_id="staged",
                    resource_id="resource-1",
                    revision="revision-2",
                    rank=1,
                ),
            )
        ),
        projection_repository=_ProjectionRepository({"resource-1": "revision-1"}),
        permission_authorizer=_PermissionAuthorizer(),
        ranking_pipeline=RankingPipeline(fusion=WeightedRrfFusion()),
    )

    hits = await retriever.retrieve(
        RagRetrievalRequest(
            query="查询",
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={},
            ),
        )
    )

    assert hits == ()


@pytest.mark.asyncio
async def test_retriever_selects_applied_revision_for_same_chunk_id() -> None:
    staged = _candidate(
        chunk_id="shared",
        resource_id="resource-1",
        revision="revision-staged",
        rank=1,
    )
    applied = _candidate(
        chunk_id="shared",
        resource_id="resource-1",
        revision="revision-applied",
        rank=2,
    )
    retriever = RagCandidateRetriever(
        embedding_client=_EmbeddingClient(),
        candidate_repository=_CandidateRepository((staged, applied)),
        projection_repository=_ProjectionRepository({"resource-1": "revision-applied"}),
        permission_authorizer=_PermissionAuthorizer(),
        ranking_pipeline=RankingPipeline(fusion=WeightedRrfFusion()),
    )

    hits = await retriever.retrieve(
        RagRetrievalRequest(
            query="查询",
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={},
            ),
        )
    )

    assert len(hits) == 1
    assert hits[0].content_revision == "revision-applied"


@pytest.mark.asyncio
async def test_retriever_drops_candidate_rejected_by_local_acl_gate() -> None:
    retriever = RagCandidateRetriever(
        embedding_client=_EmbeddingClient(),
        candidate_repository=_CandidateRepository(
            (
                _candidate(
                    chunk_id="stale-acl-hit",
                    resource_id="resource-1",
                    revision="revision-1",
                    rank=1,
                ),
            )
        ),
        projection_repository=_ProjectionRepository({"resource-1": "revision-1"}),
        permission_authorizer=_PermissionAuthorizer(("resource-1",)),
        ranking_pipeline=RankingPipeline(fusion=WeightedRrfFusion()),
    )

    hits = await retriever.retrieve(
        RagRetrievalRequest(
            query="查询",
            permission_scope=RagPermissionScope(
                user_id="revoked-user",
                group_role_map={},
            ),
        )
    )

    assert hits == ()
