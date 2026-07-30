from __future__ import annotations

import pytest

from rag.application.rag.evidence import (
    RagEvidenceMaterializer,
    RagEvidenceUnavailableError,
    RagMaterializedSource,
)
from rag.application.rag.ingestion import RagSectionReadingBlock, RagSourceRef
from rag.application.rag.retrieval import (
    RagPermissionScope,
    RagRetrievalCandidate,
)
from common.utils.chunkers import SourceSpan


class _SourceRepository:
    def __init__(
        self,
        *,
        sources: dict[tuple[str, str], RagMaterializedSource],
        blocks: dict[tuple[str, str], RagSectionReadingBlock],
    ) -> None:
        self.sources = sources
        self.blocks = blocks
        self.source_calls: list[tuple[str, tuple[str, ...]]] = []

    async def load_applied_sources(self, *, resource_id, ref_ids):
        self.source_calls.append((resource_id, ref_ids))
        return tuple(
            self.sources[(resource_id, ref_id)]
            for ref_id in ref_ids
            if (resource_id, ref_id) in self.sources
        )

    async def load_applied_reading_blocks(self, *, resource_id, reading_block_ids):
        return tuple(
            self.blocks[(resource_id, block_id)]
            for block_id in reading_block_ids
            if (resource_id, block_id) in self.blocks
        )


class _PermissionAuthorizer:
    def __init__(self, accessible: bool = True) -> None:
        self.accessible = accessible

    async def accessible_resource_ids(self, resource_ids, scope):
        return frozenset(resource_ids) if self.accessible else frozenset()


def _scope() -> RagPermissionScope:
    return RagPermissionScope(user_id="user-1", group_role_map={})


def _block(
    block_id: str,
    section_id: str,
) -> RagSectionReadingBlock:
    return RagSectionReadingBlock(
        block_id=block_id,
        section_id=section_id,
        ordinal=0,
        raw_text="正文",
        source_spans=(SourceSpan(0, 2),),
        page_labels=(),
        anchor_labels=(),
    )


def _source(
    ref_id: str,
    *,
    resource_id: str = "resource-1",
    chunk_id: str = "chunk-1",
    section_id: str = "section-1",
) -> RagMaterializedSource:
    return RagMaterializedSource(
        source_ref=RagSourceRef(
            ref_id=ref_id,
            resource_id=resource_id,
            document_version=1,
            chunk_id=chunk_id,
            section_id=section_id,
            section_path=("标题",),
            source_spans=(SourceSpan(0, 2),),
        ),
        content=f"content:{ref_id}",
    )


def _hit(
    chunk_id: str,
    ref_id: str,
    block_id: str,
    section_id: str,
    resource_id: str = "resource-1",
) -> RagRetrievalCandidate:
    return RagRetrievalCandidate(
        chunk_id=chunk_id,
        reading_block_id=block_id,
        section_id=section_id,
        section_path=("标题",),
        resource_id=resource_id,
        content_revision="revision-1",
        raw_text="正文",
        anchor_labels=(),
        source_ref_id=ref_id,
        signals=(),
    )


@pytest.mark.asyncio
async def test_materializer_promotes_best_chunk_to_one_result_per_section() -> None:
    repository = _SourceRepository(
        sources={
            ("resource-1", "ref-1"): _source("ref-1"),
            ("resource-1", "ref-2"): _source(
                "ref-2", chunk_id="chunk-2"
            ),
        },
        blocks={
            ("resource-1", "block-1"): _block("block-1", "section-1"),
            ("resource-1", "block-2"): _block("block-2", "section-1"),
        },
    )
    materializer = RagEvidenceMaterializer(
        repository=repository,
        permission_authorizer=_PermissionAuthorizer(),
    )

    results = await materializer.materialize(
        (
            _hit("chunk-1", "ref-1", "block-1", "section-1"),
            _hit("chunk-2", "ref-2", "block-2", "section-1"),
        ),
        _scope(),
    )

    assert repository.source_calls == [("resource-1", ("ref-1", "ref-2"))]
    assert len(results) == 1
    assert results[0].source.source_ref.chunk_id == "chunk-1"
    assert results[0].source.source_ref.ref_id == "ref-1"
    assert results[0].reading_block.block_id == "block-1"


@pytest.mark.asyncio
async def test_materializer_distinguishes_same_ref_id_across_resources() -> None:
    repository = _SourceRepository(
        sources={
            ("resource-1", "shared"): _source("shared"),
            ("resource-2", "shared"): _source(
                "shared",
                resource_id="resource-2",
                chunk_id="chunk-2",
                section_id="section-2",
            ),
        },
        blocks={
            ("resource-1", "block-1"): _block("block-1", "section-1"),
            ("resource-2", "block-2"): _block("block-2", "section-2"),
        },
    )
    results = await RagEvidenceMaterializer(
        repository=repository,
        permission_authorizer=_PermissionAuthorizer(),
    ).materialize(
        (
            _hit("chunk-1", "shared", "block-1", "section-1"),
            _hit("chunk-2", "shared", "block-2", "section-2", "resource-2"),
        ),
        _scope(),
    )

    assert [result.source.source_ref.resource_id for result in results] == [
        "resource-1",
        "resource-2",
    ]


@pytest.mark.asyncio
async def test_materializer_rejects_missing_source_ref() -> None:
    materializer = RagEvidenceMaterializer(
        repository=_SourceRepository(
            sources={},
            blocks={("resource-1", "block-1"): _block("block-1", "section-1")},
        ),
        permission_authorizer=_PermissionAuthorizer(),
    )

    with pytest.raises(RagEvidenceUnavailableError, match="ref-missing"):
        await materializer.materialize(
            (_hit("chunk-1", "ref-missing", "block-1", "section-1"),),
            _scope(),
        )


@pytest.mark.asyncio
async def test_materializer_fails_closed_when_permission_changed() -> None:
    materializer = RagEvidenceMaterializer(
        repository=_SourceRepository(
            sources={("resource-1", "ref-1"): _source("ref-1")},
            blocks={("resource-1", "block-1"): _block("block-1", "section-1")},
        ),
        permission_authorizer=_PermissionAuthorizer(accessible=False),
    )

    with pytest.raises(RagEvidenceUnavailableError, match="permission changed"):
        await materializer.materialize(
            (_hit("chunk-1", "ref-1", "block-1", "section-1"),),
            _scope(),
        )
