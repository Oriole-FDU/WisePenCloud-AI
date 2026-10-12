"""使用真实快照与内容 adapter 验证授权前的事实不能进入模型或命中结果。"""

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import ModelReranker, RrfFusion

from rag.application.acl.models import PermissionScope, ResourceAcl
from rag.application.retrieval import HybridRetrieval
from rag.application.snapshot import ResourceSnapshot
from rag.core.schema import ContentRevision
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.storage.vector import VectorMatch


def match(chunk, score=1.0, embedding=None, **metadata):
    return VectorMatch(
        chunk.chunk_id,
        score,
        {
            "resource_id": chunk.resource_id,
            "content_revision": chunk.content_revision,
            **metadata,
        },
        embedding,
    )


@pytest.fixture
def retrieval_deps(snapshot_reader):
    matches = []

    async def query(request):
        return [
            replace(
                item, embedding=item.embedding if request.include_embedding else None
            )
            for item in matches[: request.top_k]
        ]

    reader = SimpleNamespace(query=AsyncMock(side_effect=query))
    embedding = SimpleNamespace(
        dimensions=2, embed=AsyncMock(return_value=[[1.0, 0.0]])
    )
    client = SimpleNamespace(model="test-rerank", rerank=AsyncMock(return_value=[]))
    dependencies = {
        "vector_reader": reader,
        "embedding_client": embedding,
        "fusion": RrfFusion(),
    }
    reranker = ModelReranker(
        client=client, text_getter=lambda payload: payload.chunk.raw_text
    )
    return SimpleNamespace(
        matches=matches,
        reader=reader,
        embedding=embedding,
        client=client,
        dependencies=dependencies,
        reranker=reranker,
        usecase=HybridRetrieval(
            snapshot_reader=snapshot_reader,
            **dependencies,
            reranker=reranker,
        ),
    )


@pytest.fixture
async def visible_chunks(
    make_read_document, publish_read_document, make_read_chunk, storage, read_acls
):
    document = await publish_read_document(make_read_document("visible"))
    chunks = [
        replace(
            make_read_chunk(document, index),
            section_id="section",
            section_path=["标题"],
            raw_text=f"正文 {index}",
            metadata={"contextual_context": "内部上下文"},
        )
        for index in range(3)
    ]
    await storage.upsert_chunks(chunks)
    read_acls["visible"] = ResourceAcl("visible", 1, "reader")
    return chunks


@pytest.mark.parametrize(
    "hidden_state", ["missing", "staged", "destroyed", "missing-acl", "denied"]
)
async def test_application_only_exposes_applied_authorized_chunk_text(
    retrieval_deps,
    visible_chunks,
    hidden_state,
    make_read_document,
    make_read_chunk,
    publish_read_document,
    publication_store,
    storage,
    read_acls,
    read_acl_repository,
):
    hidden_doc = make_read_document("hidden")
    hidden = make_read_chunk(hidden_doc)
    if hidden_state == "staged":
        await publication_store.stage_revision(
            ContentRevision.create(
                resource_id="hidden",
                document_version=1,
                text=hidden_doc.text,
            )
        )
    elif hidden_state in {"destroyed", "missing-acl", "denied"}:
        await publish_read_document(hidden_doc)
    if hidden_state == "destroyed":
        await publication_store.destroy_resources(["hidden"])
    elif hidden_state == "denied":
        read_acls["hidden"] = ResourceAcl("hidden", 1, "someone-else")
    await storage.upsert_chunks([hidden])
    retrieval_deps.matches[:] = [
        match(hidden),
        match(visible_chunks[1]),
        match(visible_chunks[0]),
    ]
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    hits = await retrieval_deps.usecase.retrieve("query", snapshot=snapshot, top_k=5)
    assert [hit.chunk_id for hit in hits] == [
        visible_chunks[1].chunk_id,
        visible_chunks[0].chunk_id,
    ]
    assert [hit.rank for hit in hits] == [1, 2]
    assert retrieval_deps.client.rerank.call_args.kwargs["documents"] == [
        "正文 1",
        "正文 0",
    ]
    assert set(asdict(hits[0])) == {
        "chunk_id",
        "resource_id",
        "content_revision",
        "section_id",
        "section_path",
        "text",
        "rank",
        "score",
        "seed_entities",
    }
    assert hits[0].section_path == ["标题"] and hits[0].section_id == "section"
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1
    # 两条召回查询之后不会因为部分不可见而补召回。
    assert retrieval_deps.reader.query.await_count == 2


@pytest.mark.parametrize(
    "bad_metadata",
    [
        {},
        {"resource_id": "visible"},
        {"resource_id": 1, "content_revision": "revision"},
        {"resource_id": "visible", "content_revision": " "},
    ],
)
async def test_missing_projection_identity_is_filtered_before_content_io(
    retrieval_deps, visible_chunks, storage, bad_metadata
):
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    retrieval_deps.matches[:] = [
        VectorMatch(visible_chunks[0].chunk_id, 1, bad_metadata)
    ]
    assert (
        await retrieval_deps.usecase.retrieve(
            "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
        )
        == []
    )
    storage.get_chunks.assert_not_awaited()
    retrieval_deps.client.rerank.assert_not_awaited()


@pytest.mark.parametrize("bad_kind", ["resource", "revision", "missing-fact", "old"])
async def test_projection_mismatch_old_revision_and_missing_facts_do_not_fall_back(
    retrieval_deps,
    visible_chunks,
    bad_kind,
    make_read_document,
    make_read_chunk,
    storage,
):
    visible = visible_chunks[0]
    if bad_kind == "resource":
        bad = match(visible, resource_id="wrong")
    elif bad_kind == "revision":
        bad = match(visible, content_revision="wrong")
    elif bad_kind == "missing-fact":
        bad = replace(match(visible), record_id="missing")
    else:
        old = make_read_chunk(make_read_document("visible", version=0))
        await storage.upsert_chunks([old])
        bad = match(old)
    retrieval_deps.matches[:] = [bad, match(visible_chunks[1])]
    hits = await retrieval_deps.usecase.retrieve(
        "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
    )
    assert [hit.chunk_id for hit in hits] == [visible_chunks[1].chunk_id]
    assert retrieval_deps.client.rerank.call_args.kwargs["documents"] == ["正文 1"]


async def test_snapshot_from_document_read_is_reused_across_version_acl_and_destroy_changes(
    retrieval_deps,
    visible_chunks,
    snapshot_reader,
    publication_store,
    read_acl_repository,
    make_read_document,
    publish_read_document,
    make_read_chunk,
    storage,
    read_acls,
):
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    await snapshot_reader.read_documents(["visible"], snapshot=snapshot)
    newer_doc = await publish_read_document(make_read_document("visible", version=2))
    newer_chunk = make_read_chunk(newer_doc)
    await storage.upsert_chunks([newer_chunk])
    read_acls["visible"] = ResourceAcl("visible", 2, "someone-else")
    retrieval_deps.matches[:] = [match(newer_chunk), match(visible_chunks[0])]
    hits = await retrieval_deps.usecase.retrieve("query", snapshot=snapshot)
    assert [hit.chunk_id for hit in hits] == [visible_chunks[0].chunk_id]
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1
    await publication_store.destroy_resources(["visible"])
    assert await retrieval_deps.usecase.retrieve("query", snapshot=snapshot) == hits
    assert publication_store.get_states.await_count == 1
    assert (
        await retrieval_deps.usecase.retrieve(
            "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
        )
        == []
    )


async def test_first_denial_stays_fixed_but_new_snapshot_observes_acl_change(
    retrieval_deps, visible_chunks, read_acls
):
    retrieval_deps.matches[:] = [match(visible_chunks[0])]
    read_acls["visible"] = ResourceAcl("visible", 1, "someone-else")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await retrieval_deps.usecase.retrieve("query", snapshot=snapshot) == []
    read_acls["visible"] = ResourceAcl("visible", 2, "reader")
    assert await retrieval_deps.usecase.retrieve("query", snapshot=snapshot) == []
    assert (
        len(
            await retrieval_deps.usecase.retrieve(
                "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
            )
        )
        == 1
    )


async def test_new_snapshot_observes_new_version_without_mixing_old_chunks(
    retrieval_deps,
    visible_chunks,
    make_read_document,
    publish_read_document,
    make_read_chunk,
    storage,
):
    old_snapshot = ResourceSnapshot(PermissionScope("reader"))
    retrieval_deps.matches[:] = [match(visible_chunks[0])]
    old_hits = await retrieval_deps.usecase.retrieve("query", snapshot=old_snapshot)
    newer = make_read_chunk(
        await publish_read_document(make_read_document("visible", version=2))
    )
    await storage.upsert_chunks([newer])
    retrieval_deps.matches[:] = [match(newer), match(visible_chunks[0])]
    old_again = await retrieval_deps.usecase.retrieve("query", snapshot=old_snapshot)
    # 快照固定版本和授权，不固定再次召回时产生的排名分数。
    assert [hit.chunk_id for hit in old_again] == [hit.chunk_id for hit in old_hits]
    assert old_again[0].score == pytest.approx(2 / 62)
    hits = await retrieval_deps.usecase.retrieve(
        "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
    )
    assert [hit.chunk_id for hit in hits] == [newer.chunk_id]


@pytest.mark.parametrize("diversity", ["off", "moderate", "high"])
async def test_call_configuration_focus_and_diversity_are_explicit(
    retrieval_deps, visible_chunks, diversity
):
    retrieval_deps.matches[:] = [
        match(visible_chunks[0], embedding=[1.0, 0.0]),
        match(visible_chunks[1], embedding=[1.0, 0.0]),
        match(visible_chunks[2], embedding=[0.0, 1.0]),
    ]
    filters = MetadataFilters([MetadataFilter("category", "paper")])
    hits = await retrieval_deps.usecase.retrieve(
        " query ",
        snapshot=ResourceSnapshot(PermissionScope("reader")),
        top_k=2,
        focus=" focus ",
        diversity=diversity,
        filters=filters,
        dense_top_k=8,
        sparse_top_k=9,
        candidate_limit=10,
    )
    requests = [call.args[0] for call in retrieval_deps.reader.query.call_args_list]
    assert [(request.top_k, request.embedding is not None) for request in requests] == [
        (8, True),
        (9, False),
    ]
    assert all(request.filters is filters for request in requests)
    assert all(
        request.include_embedding == (diversity != "off") for request in requests
    )
    assert retrieval_deps.client.rerank.call_args.kwargs["query"] == "focus"
    assert [hit.chunk_id for hit in hits] == [
        visible_chunks[0].chunk_id,
        visible_chunks[2 if diversity == "high" else 1].chunk_id,
    ]
    assert [hit.rank for hit in hits] == [1, 2]
    assert retrieval_deps.embedding.embed.await_count == 1
    if diversity == "off":
        assert hits[0].score == pytest.approx(2 / 61)


async def test_candidate_limit_applies_before_preparation_and_reranking(
    retrieval_deps, visible_chunks
):
    retrieval_deps.matches[:] = [match(chunk) for chunk in visible_chunks]
    hits = await retrieval_deps.usecase.retrieve(
        "query", snapshot=ResourceSnapshot(PermissionScope("reader")), candidate_limit=2
    )
    assert [hit.chunk_id for hit in hits] == [
        visible_chunks[0].chunk_id,
        visible_chunks[1].chunk_id,
    ]
    assert retrieval_deps.client.rerank.call_args.kwargs["documents"] == [
        "正文 0",
        "正文 1",
    ]


@pytest.mark.parametrize("failure", ["read", "diversity"])
async def test_usecase_failure_propagates_at_owning_stage(
    retrieval_deps, visible_chunks, snapshot_reader, storage, failure
):
    retrieval_deps.matches[:] = [match(visible_chunks[0])]
    error = OSError("failed")
    if failure == "read":
        storage.get_chunks = AsyncMock(side_effect=error)
    usecase = HybridRetrieval(
        snapshot_reader=snapshot_reader,
        **retrieval_deps.dependencies,
        reranker=retrieval_deps.reranker,
    )
    if failure == "diversity":
        with pytest.raises(RuntimeError, match="embedding"):
            await usecase.retrieve(
                "query",
                snapshot=ResourceSnapshot(PermissionScope("reader")),
                diversity="high",
            )
    else:
        with pytest.raises(OSError) as caught:
            await usecase.retrieve(
                "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
            )
        assert caught.value is error
    if failure == "read":
        retrieval_deps.client.rerank.assert_not_awaited()


async def test_empty_recall_skips_content_and_model(
    retrieval_deps, snapshot_reader, storage
):
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    usecase = HybridRetrieval(
        snapshot_reader=snapshot_reader,
        **retrieval_deps.dependencies,
        reranker=retrieval_deps.reranker,
    )
    assert (
        await usecase.retrieve(
            "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
        )
        == []
    )
    storage.get_chunks.assert_not_awaited()
    retrieval_deps.client.rerank.assert_not_awaited()


async def test_one_shared_usecase_serves_concurrent_snapshots_and_configs(
    retrieval_deps, visible_chunks, read_acl_repository
):
    retrieval_deps.matches[:] = [match(visible_chunks[0], embedding=[1.0, 0.0])]
    allowed, denied = await asyncio.gather(
        retrieval_deps.usecase.retrieve(
            "allowed",
            snapshot=ResourceSnapshot(PermissionScope("reader")),
            dense_top_k=7,
        ),
        retrieval_deps.usecase.retrieve(
            "denied",
            snapshot=ResourceSnapshot(PermissionScope("other")),
            dense_top_k=9,
            diversity="high",
        ),
    )
    assert len(allowed) == 1 and denied == []
    dense_requests = [
        call.args[0]
        for call in retrieval_deps.reader.query.call_args_list
        if call.args[0].embedding is not None
    ]
    assert {
        (request.top_k, request.include_embedding) for request in dense_requests
    } == {(7, False), (9, True)}
    assert retrieval_deps.client.rerank.await_count == 1
    assert retrieval_deps.client.rerank.call_args.kwargs["query"] == "allowed"
    assert read_acl_repository.get_resource_acls.await_count == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"top_k": 0},
        {"diversity": "unknown"},
        {"dense_top_k": 0},
        {"sparse_top_k": 0},
        {"candidate_limit": 0},
    ],
)
async def test_invalid_call_fails_before_backend_io(retrieval_deps, kwargs):
    with pytest.raises(ValueError):
        await retrieval_deps.usecase.retrieve(
            snapshot=ResourceSnapshot(PermissionScope("reader")),
            **{"query": "query", **kwargs},
        )
    retrieval_deps.reader.query.assert_not_awaited()
    retrieval_deps.embedding.embed.assert_not_awaited()
