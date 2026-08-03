from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from rag.application.rag.acl import RagResourceAclProjection
from rag.application.rag.ingestion import (
    ContextIndexingError,
    RagContentIndexer,
    RagContentProjection,
    RagDocumentContent,
    RagSectionProjector,
    RagContentIndexingError,
    RagProjectionCheckpoint,
    RagProjectionStage,
    RagProjectionStageAction,
    prepare_projection_stage,
)


@dataclass(frozen=True, slots=True)
class FakeEmbeddingResult:
    embeddings: list[list[float]]


class FakeProjectionRepository:
    def __init__(self) -> None:
        self.checkpoint: RagProjectionCheckpoint | None = None
        self.applied: list[RagProjectionStage] = []
        self.staged_projections: list[RagContentProjection] = []

    async def get_checkpoint(
        self,
        resource_id: str,
    ) -> RagProjectionCheckpoint | None:
        return self.checkpoint

    async def stage_projection(
        self,
        projection: RagContentProjection,
    ) -> RagProjectionStage:
        self.staged_projections.append(projection)
        return prepare_projection_stage(projection, self.checkpoint)

    async def apply_projection(self, stage: RagProjectionStage) -> None:
        self.applied.append(stage)


class FakeAclRepository:
    def __init__(
        self,
        *,
        local: RagResourceAclProjection | None = None,
        authoritative: RagResourceAclProjection | None = None,
    ) -> None:
        self.local = local
        self.authoritative = authoritative
        self.upserted: list[RagResourceAclProjection] = []

    async def get_projection(self, resource_id: str) -> RagResourceAclProjection | None:
        return self.local

    async def load_authoritative_projection(
        self,
        resource_id: str,
    ) -> RagResourceAclProjection | None:
        return self.authoritative

    async def upsert_projection(self, projection: RagResourceAclProjection) -> None:
        self.upserted.append(projection)


class FakeEmbeddingClient:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings
        self.inputs: list[list[str]] = []

    async def aembed(self, input: Sequence[str]) -> FakeEmbeddingResult:
        texts = list(input)
        self.inputs.append(texts)
        return FakeEmbeddingResult(embeddings=self.embeddings)


class FakeContextIndexingService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def contextualize(
        self,
        projection: RagContentProjection,
    ) -> RagContentProjection:
        if self.error is not None:
            raise self.error
        return projection


class FakeVectorRepository:
    def __init__(
        self,
        *,
        fail_upsert: bool = False,
        reusable_vectors: Mapping[str, Sequence[float]] | None = None,
    ) -> None:
        self.fail_upsert = fail_upsert
        self.reusable_vectors = reusable_vectors or {}
        self.upserts: list[
            tuple[RagProjectionStage, RagResourceAclProjection | None]
        ] = []
        self.deleted: list[tuple[str, str]] = []

    async def load_reusable_vectors(
        self,
        projection: RagContentProjection,
    ) -> Mapping[str, Sequence[float]]:
        return self.reusable_vectors

    async def upsert_staged_projection(
        self,
        *,
        projection: RagContentProjection,
        stage: RagProjectionStage,
        dense_vectors: Mapping[str, Sequence[float]],
        acl_projection: RagResourceAclProjection | None,
    ) -> None:
        if self.fail_upsert:
            raise RuntimeError("qdrant unavailable")
        assert set(dense_vectors) == {
            chunk.chunk_id for chunk in projection.retrieval_chunks
        }
        self.upserts.append((stage, acl_projection))

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        self.deleted.append((resource_id, keep_content_revision))


def _acl() -> RagResourceAclProjection:
    return RagResourceAclProjection(
        resource_id="resource-1",
        acl_revision=1,
        owner_id="owner-1",
    )


def _content() -> RagDocumentContent:
    return RagDocumentContent("resource-1", 1, "# 标题\n\n正文。")


@pytest.mark.asyncio
async def test_content_indexer_applies_only_after_vector_write() -> None:
    projection_repository = FakeProjectionRepository()
    vector_repository = FakeVectorRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=vector_repository,
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=FakeEmbeddingClient([[0.1, 0.2]]),
        context_indexing=FakeContextIndexingService(),
    )

    result = await indexer.index(_content())

    assert result.indexed_chunk_count == 1
    assert result.stage.action is RagProjectionStageAction.STAGED
    assert vector_repository.upserts == [(result.stage, _acl())]
    assert projection_repository.applied == [result.stage]
    assert vector_repository.deleted == [
        (result.stage.resource_id, result.stage.content_revision)
    ]


@pytest.mark.asyncio
async def test_content_indexer_refreshes_missing_acl_from_authoritative_resource() -> (
    None
):
    acl_repository = FakeAclRepository(authoritative=_acl())
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=FakeProjectionRepository(),
        checkpoint_repository=FakeProjectionRepository(),
        vector_repository=FakeVectorRepository(),
        acl_repository=acl_repository,
        embedding_client=FakeEmbeddingClient([[0.1, 0.2]]),
        context_indexing=FakeContextIndexingService(),
    )

    await indexer.index(_content())

    assert acl_repository.upserted == [_acl()]


@pytest.mark.asyncio
async def test_content_indexer_does_not_apply_without_acl() -> None:
    projection_repository = FakeProjectionRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=FakeVectorRepository(),
        acl_repository=FakeAclRepository(),
        embedding_client=FakeEmbeddingClient([[0.1, 0.2]]),
        context_indexing=FakeContextIndexingService(),
    )

    with pytest.raises(RagContentIndexingError, match="ACL"):
        await indexer.index(_content())

    assert projection_repository.applied == []


@pytest.mark.asyncio
async def test_content_indexer_does_not_apply_after_vector_failure() -> None:
    projection_repository = FakeProjectionRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=FakeVectorRepository(fail_upsert=True),
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=FakeEmbeddingClient([[0.1, 0.2]]),
        context_indexing=FakeContextIndexingService(),
    )

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await indexer.index(_content())

    assert projection_repository.applied == []


@pytest.mark.asyncio
async def test_content_indexer_rejects_incomplete_embedding_response() -> None:
    projection_repository = FakeProjectionRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=FakeVectorRepository(),
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=FakeEmbeddingClient([]),
        context_indexing=FakeContextIndexingService(),
    )

    with pytest.raises(RagContentIndexingError, match="embedding response count"):
        await indexer.index(_content())

    assert projection_repository.applied == []


@pytest.mark.asyncio
async def test_content_indexer_only_embeds_missing_chunks() -> None:
    projector = RagSectionProjector()
    projection = projector.project(_content())
    reusable = {projection.retrieval_chunks[0].chunk_id: [0.3, 0.4]}
    embedding_client = FakeEmbeddingClient([])
    indexer = RagContentIndexer(
        projector=projector,
        projection_repository=FakeProjectionRepository(),
        checkpoint_repository=FakeProjectionRepository(),
        vector_repository=FakeVectorRepository(reusable_vectors=reusable),
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=embedding_client,
        context_indexing=FakeContextIndexingService(),
    )

    result = await indexer.index(_content())

    assert embedding_client.inputs == []
    assert result.embedded_chunk_count == 0
    assert result.reused_vector_count == 1


@pytest.mark.asyncio
async def test_already_applied_retry_only_cleans_old_revisions() -> None:
    projection = RagSectionProjector().project(_content())
    stage = prepare_projection_stage(projection, None)
    projection_repository = FakeProjectionRepository()
    projection_repository.checkpoint = RagProjectionCheckpoint(
        resource_id=stage.resource_id,
        applied_content_revision=stage.content_revision,
        applied_document_version=stage.document_version,
    )
    embedding_client = FakeEmbeddingClient([[0.1, 0.2]])
    vector_repository = FakeVectorRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=vector_repository,
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=embedding_client,
        context_indexing=FakeContextIndexingService(),
    )

    result = await indexer.index(_content())

    assert result.stage.action is RagProjectionStageAction.ALREADY_APPLIED
    assert embedding_client.inputs == []
    assert vector_repository.upserts == []
    assert projection_repository.applied == []
    assert vector_repository.deleted == [(stage.resource_id, stage.content_revision)]


@pytest.mark.asyncio
async def test_context_indexing_failure_does_not_stage_projection() -> None:
    projection_repository = FakeProjectionRepository()
    indexer = RagContentIndexer(
        projector=RagSectionProjector(),
        projection_repository=projection_repository,
        checkpoint_repository=projection_repository,
        vector_repository=FakeVectorRepository(),
        acl_repository=FakeAclRepository(local=_acl()),
        embedding_client=FakeEmbeddingClient([[0.1, 0.2]]),
        context_indexing=FakeContextIndexingService(
            error=ContextIndexingError("context service unavailable")
        ),
    )

    with pytest.raises(ContextIndexingError, match="context service unavailable"):
        await indexer.index(_content())

    assert projection_repository.staged_projections == []
