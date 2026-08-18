from dataclasses import dataclass

import pytest

from rag.application.rag.index import ResourceIndexer
from rag.domain.models.acl import ResourceAcl
from rag.domain.repositories import StageAction


@dataclass
class _EmbeddingResult:
    embeddings: list[list[float]]


class _Failure:
    def __init__(self, step: str | None = None) -> None:
        self.step = step
        self.failed = False
        self.calls: list[str] = []

    def hit(self, step: str) -> None:
        self.calls.append(step)
        if self.step == step and not self.failed:
            self.failed = True
            raise RuntimeError(step)


class _ContextualText:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure

    async def contextualize(self, **kwargs):
        self._failure.hit("contextual")
        return list(kwargs["chunks"])


class _EmbeddingClient:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure

    async def aembed(self, input):
        self._failure.hit("embedding")
        return _EmbeddingResult([[0.1, 0.2] for _ in input])


class _AclRefresher:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure

    async def refresh(self, resource_id):
        self._failure.hit("acl_refresh")


class _AclReader:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure
        self.acl = ResourceAcl(
            resource_id="resource-1",
            acl_revision=1,
            owner_id="owner-1",
        )

    async def get_resource_acl(self, resource_id):
        self._failure.hit("acl_read")
        return self.acl if resource_id == self.acl.resource_id else None

    async def get_resource_acls(self, resource_ids):
        self._failure.hit("acl_read")
        return {self.acl.resource_id: self.acl}


class _ResourceWriter:
    def __init__(self, failure: _Failure, *, stale: bool = False) -> None:
        self._failure = failure
        self.stale = stale
        self.applied = False

    async def stage_revision(self, **kwargs):
        assert kwargs["structure"].total_length == len(kwargs["markdown"])
        self._failure.hit("mongo_stage")
        if self.stale:
            return StageAction.STALE
        return StageAction.ALREADY_APPLIED if self.applied else StageAction.STAGED

    async def apply_revision(self, revision):
        self._failure.hit("mongo_apply")
        self.applied = True

    async def delete_other_revisions(self, **kwargs):
        self._failure.hit("mongo_cleanup")


class _RetrievalWriter:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure

    async def load_reusable_vectors(self, **kwargs):
        self._failure.hit("vector_reuse")
        return {}

    async def write_staged_revision(self, **kwargs):
        self._failure.hit("qdrant_stage")

    async def activate_revision(self, **kwargs):
        self._failure.hit("qdrant_activate")

    async def delete_other_revisions(self, **kwargs):
        self._failure.hit("qdrant_cleanup")


class _GraphExtractor:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure

    async def extract(self, **kwargs):
        self._failure.hit("graph_extract")
        return []


class _GraphWriter:
    def __init__(self, failure: _Failure) -> None:
        self._failure = failure
        self.published = 0
        self.skipped = 0

    async def begin_build(self, **kwargs):
        self._failure.hit("graph_begin")

    async def publish(self, **kwargs):
        self._failure.hit("graph_publish")
        self.published += 1

    async def skip(self, **kwargs):
        self._failure.hit("graph_skip")
        self.skipped += 1


def _indexer(
    failure: _Failure,
    *,
    stale: bool = False,
    contextual_enabled: bool = True,
    graph_enabled: bool = True,
) -> tuple[ResourceIndexer, _ResourceWriter, _GraphWriter]:
    resource_writer = _ResourceWriter(failure, stale=stale)
    graph_writer = _GraphWriter(failure)
    return (
        ResourceIndexer(
            contextual_text=_ContextualText(failure),
            embedding_client=_EmbeddingClient(failure),
            acl_refresher=_AclRefresher(failure),
            acl_reader=_AclReader(failure),
            resource_writer=resource_writer,
            retrieval_writer=_RetrievalWriter(failure),
            graph_extractor=_GraphExtractor(failure),
            graph_repository=graph_writer,
            contextual_enabled=contextual_enabled,
            graph_enabled=graph_enabled,
        ),
        resource_writer,
        graph_writer,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_step",
    [
        "mongo_stage",
        "contextual",
        "acl_refresh",
        "acl_read",
        "vector_reuse",
        "embedding",
        "qdrant_stage",
        "mongo_apply",
        "qdrant_activate",
        "qdrant_cleanup",
        "graph_begin",
        "graph_extract",
        "graph_publish",
        "mongo_cleanup",
    ],
)
async def test_sectioned_indexing_retry_completes_after_each_step_failure(
    failed_step: str,
) -> None:
    failure = _Failure(failed_step)
    indexer, resource_writer, graph_writer = _indexer(failure)

    with pytest.raises(RuntimeError, match=failed_step):
        await indexer.index_resource(
            resource_id="resource-1",
            document_version=1,
            markdown="# Title\n\nBody text.",
        )

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown="# Title\n\nBody text.",
    )

    assert action in (StageAction.STAGED, StageAction.ALREADY_APPLIED)
    assert resource_writer.applied is True
    assert graph_writer.published == (2 if failed_step == "mongo_cleanup" else 1)
    assert "mongo_cleanup" in failure.calls

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("markdown", "expected_mode"),
    [("Plain body.", "flat_text"), ("\n\n", "empty")],
)
async def test_unsectioned_indexing_publishes_content_and_skips_graph(
    markdown: str,
    expected_mode: str,
) -> None:
    failure = _Failure()
    indexer, resource_writer, graph_writer = _indexer(failure)

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown=markdown,
    )

    assert action is StageAction.STAGED
    assert resource_writer.applied is True
    assert graph_writer.skipped == 1
    assert "graph_extract" not in failure.calls
    assert expected_mode in {"flat_text", "empty"}


@pytest.mark.asyncio
async def test_graph_disabled_skips_generation_and_all_graph_writes() -> None:
    failure = _Failure()
    indexer, resource_writer, graph_writer = _indexer(
        failure,
        graph_enabled=False,
    )

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown="# Title\n\nBody text.",
    )

    assert action is StageAction.STAGED
    assert resource_writer.applied is True
    assert graph_writer.published == 0
    assert graph_writer.skipped == 0
    assert not any(step.startswith("graph_") for step in failure.calls)


@pytest.mark.asyncio
async def test_contextual_disabled_skips_contextual_call() -> None:
    failure = _Failure()
    indexer, resource_writer, graph_writer = _indexer(
        failure,
        contextual_enabled=False,
    )

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown="# Title\n\nBody text.",
    )

    assert action is StageAction.STAGED
    assert "contextual" not in failure.calls


@pytest.mark.asyncio
async def test_stale_event_stops_before_external_publication() -> None:
    failure = _Failure()
    indexer, resource_writer, graph_writer = _indexer(failure, stale=True)

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown="# Title\n\nBody text.",
    )

    assert action is StageAction.STALE
    assert resource_writer.applied is False
    assert graph_writer.published == 0
    assert failure.calls == ["mongo_stage"]


@pytest.mark.asyncio
async def test_graph_skip_failure_is_compensated_by_event_retry() -> None:
    failure = _Failure("graph_skip")
    indexer, resource_writer, graph_writer = _indexer(failure)

    with pytest.raises(RuntimeError, match="graph_skip"):
        await indexer.index_resource(
            resource_id="resource-1",
            document_version=1,
            markdown="Plain body.",
        )

    action = await indexer.index_resource(
        resource_id="resource-1",
        document_version=1,
        markdown="Plain body.",
    )

    assert action is StageAction.ALREADY_APPLIED
    assert resource_writer.applied is True
    assert graph_writer.skipped == 1
