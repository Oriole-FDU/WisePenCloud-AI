"""完整检索装配示例：使用真实 adapter 和用例，只替换驱动 I/O 与模型响应。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.utils.ai_clients import EmbeddingClient
from common.utils.retrieval import ModelReranker
from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.application.publication import DocumentReadyEvent
from rag.application.reading import DocumentReading, SectionReadScope
from rag.application.chunk import ChunkRetrieval
from rag.domain.models import ContentRevision
from rag.application.publication.ingestion import MarkdownChunker


@pytest.mark.parametrize("diversity", ["off", "high"])
async def test_storage_to_vector_retrieval_composition(
        storage, publication_store, vectors, qdrant_client, document, diversity
):
    # storage/publication_store 是实际 Mongo adapter；vectors 是实际 Qdrant adapter。
    # fixture 只把数据库 collection 和 Qdrant 网络客户端替换为本地测试边界。
    document = DocumentReadyEvent.model_validate(
        {
            "resourceId": document.resource_id,
            "version": document.document_version,
            "content": document.text,
        }
    ).to_document()
    chunk = (await MarkdownChunker().split(document))[0]
    revision = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version,
        text=document.text,
    )
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])
    await publication_store.stage_revision(revision)
    await publication_store.apply_revision(revision)

    acl_repository = SimpleNamespace(
        get_resource_acls=AsyncMock(
            return_value={
                document.resource_id: ResourceAcl(document.resource_id, 1, "reader")
            }
        )
    )
    snapshot_reader = ResourceSnapshotReader(
        store=publication_store,
        resource_acls=acl_repository,
        reader=storage,
    )

    # 向量后端返回定位与可选已存向量，正文仍由 SnapshotReader 从 Mongo 读取。
    qdrant_client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                payload={
                    "record_id": chunk.chunk_id,
                    "metadata": {
                        "resource_id": chunk.resource_id,
                        "content_revision": chunk.content_revision,
                    },
                },
                score=0.8,
                vector={"dense": [1.0, 0.0]},
            )
        ]
    )
    embedding_request = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])]
        )
    )
    # 使用真实 Common 客户端；只替换供应商 I/O，模型配置在初始化时绑定。
    embedding_client = EmbeddingClient(
        SimpleNamespace(embeddings=SimpleNamespace(create=embedding_request)),
        model="demo-embedding",
        dimensions=2,
    )
    rerank_client = SimpleNamespace(
        model="demo-rerank",
        rerank=AsyncMock(return_value=[{"index": 0, "relevance_score": 0.9}]),
    )

    # 用例绑定实际仓储与模型客户端，调用时直接 from_storage 组装本次 Retriever。
    usecase = ChunkRetrieval(
        snapshot_reader=snapshot_reader,
        vector_reader=vectors,
        embedding_client=embedding_client,
        reranker=ModelReranker(
            client=rerank_client,
            text_getter=lambda payload: payload.chunk.raw_text,
        ),
    )

    # 可信身份只绑定到本次 snapshot，不进入 Retriever 或模型客户端的构造参数。
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    hits = await usecase.retrieve(
        "中文正文",
        snapshot=snapshot,
        dense_top_k=7,
        sparse_top_k=9,
        top_k=1,
        diversity=diversity,
    )

    assert len(hits) == 1
    assert hits[0].chunk_id == chunk.chunk_id
    assert hits[0].text == chunk.raw_text
    assert hits[0].rank == 1
    assert hits[0].score == pytest.approx(0.9 if diversity == "off" else 0.5)
    embedding_request.assert_awaited_once_with(
        model="demo-embedding", input=["中文正文"], dimensions=2
    )
    rerank_client.rerank.assert_awaited_once_with(
        query="中文正文", documents=[chunk.raw_text], instruct=None, top_n=None
    )
    calls = [call.kwargs for call in qdrant_client.query_points.call_args_list]
    assert [(call["using"], call["limit"]) for call in calls] == [
        ("dense", 7),
        ("sparse", 9),
    ]
    assert all(
        call["with_vectors"] == (["dense"] if diversity == "high" else False)
        for call in calls
    )
    acl_repository.get_resource_acls.assert_awaited_once_with([document.resource_id])

    # 对外只传检索返回的章节 ID；目录、邻域与原文读取显式共享同一快照。
    reading = DocumentReading(reader=snapshot_reader)
    outline = await reading.global_outline(document.resource_id, snapshot=snapshot)
    section_id = hits[0].section_id
    assert f"id={section_id}" in outline.outline
    neighborhood = await reading.neighborhood([section_id], snapshot=snapshot)
    assert neighborhood[0].section_id == section_id and "[C]" in neighborhood[0].outline
    section = next(
        item for item in document.structure.sections if item.section_id == section_id
    )
    for scope in SectionReadScope:
        result = await reading.read_sections(
            [section_id], snapshot=snapshot, scope=scope
        )
        span = (
            section.own_span if scope is SectionReadScope.OWN else section.subtree_span
        )
        assert result[0].text == document.text[span.start_offset: span.end_offset]
    acl_repository.get_resource_acls.assert_awaited_once_with([document.resource_id])
