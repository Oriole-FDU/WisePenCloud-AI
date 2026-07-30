from __future__ import annotations

import pytest

from rag.application.rag.acl import RagResourceAclProjection
from rag.application.rag.graph_extraction import KnowledgeExtractionSource
from rag.application.rag.graph_projection import (
    KnowledgeGraphIndexAction,
    KnowledgeGraphIndexer,
)
from rag.application.rag.ingestion import RagProjectionCheckpoint
from rag.application.rag.repositories import KnowledgeGraphProjectionSupersededError


class _ContentRepository:
    def __init__(self, revisions: tuple[str, ...] = ("revision-1",)) -> None:
        self.revisions = iter(revisions)
        self.last_revision = revisions[-1]

    async def load_applied_extraction_source(self, resource_id):
        return KnowledgeExtractionSource(
            resource_id=resource_id,
            document_version=1,
            content_revision="revision-1",
            markdown="",
            chunks=(),
            source_refs=(),
        )

    async def get_checkpoint(self, resource_id):
        revision = next(self.revisions, self.last_revision)
        return RagProjectionCheckpoint(
            resource_id=resource_id,
            applied_content_revision=revision,
        )


class _AclRepository:
    async def get_projection(self, resource_id):
        return RagResourceAclProjection(
            resource_id=resource_id,
            acl_revision=1,
            owner_id="owner-1",
        )

    async def load_authoritative_projection(self, resource_id):
        return None


class _Extractor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def extract(self, windows):
        assert windows == ()
        self.events.append("extract")
        return ()


class _GraphRepository:
    def __init__(
        self,
        events: list[str],
        *,
        applied: bool = False,
        superseded: bool = False,
    ) -> None:
        self.events = events
        self.applied = applied
        self.superseded = superseded
        self.projection = None

    async def is_projection_applied(self, **kwargs):
        return self.applied

    async def invalidate_projection(self, **kwargs):
        self.events.append("invalidate")

    async def update_acl_projection(self, projection):
        self.events.append("acl")

    async def apply_projection(self, *, projection):
        if self.superseded:
            raise KnowledgeGraphProjectionSupersededError("superseded")
        self.events.append("apply")
        self.projection = projection


@pytest.mark.asyncio
async def test_graph_indexer_invalidates_before_extraction_and_applies_empty_graph() -> (
    None
):
    events: list[str] = []
    graph_repository = _GraphRepository(events)
    indexer = KnowledgeGraphIndexer(
        content_repository=_ContentRepository(),
        acl_repository=_AclRepository(),
        extractor=_Extractor(events),
        graph_repository=graph_repository,
    )

    result = await indexer.index(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert events == ["invalidate", "extract", "acl", "apply"]
    assert result.action is KnowledgeGraphIndexAction.APPLIED
    assert result.projected_relation_count == 0
    assert graph_repository.projection is not None
    assert graph_repository.projection.content_revision == "revision-1"


@pytest.mark.asyncio
async def test_graph_indexer_skips_already_applied_revision() -> None:
    events: list[str] = []
    indexer = KnowledgeGraphIndexer(
        content_repository=_ContentRepository(),
        acl_repository=_AclRepository(),
        extractor=_Extractor(events),
        graph_repository=_GraphRepository(events, applied=True),
    )

    result = await indexer.index(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert result.action is KnowledgeGraphIndexAction.ALREADY_APPLIED
    assert events == []


@pytest.mark.asyncio
async def test_graph_indexer_drops_extraction_when_content_changed() -> None:
    events: list[str] = []
    graph_repository = _GraphRepository(events)
    indexer = KnowledgeGraphIndexer(
        content_repository=_ContentRepository(("revision-1", "revision-2")),
        acl_repository=_AclRepository(),
        extractor=_Extractor(events),
        graph_repository=graph_repository,
    )

    result = await indexer.index(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert result.action is KnowledgeGraphIndexAction.STALE
    assert events == ["invalidate", "extract"]


@pytest.mark.asyncio
async def test_graph_indexer_maps_neo4j_cas_failure_to_stale() -> None:
    events: list[str] = []
    indexer = KnowledgeGraphIndexer(
        content_repository=_ContentRepository(),
        acl_repository=_AclRepository(),
        extractor=_Extractor(events),
        graph_repository=_GraphRepository(events, superseded=True),
    )

    result = await indexer.index(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert result.action is KnowledgeGraphIndexAction.STALE
    assert events == ["invalidate", "extract", "acl"]
