"""P1-B 文档增强、双向量投影和发布完整性测试。"""

import json
from types import SimpleNamespace

import pytest
from common.utils.document import SourceSpan

from rag_v3.application.document import DocumentIndexBuilder, DocumentPreparer
from rag_v3.application.document.indexing import _shared_window
from rag_v3.application.publication import DocumentPublication
from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.models import ContentRevision, DocChunk, rag_chunk_id
from rag_v3.domain.repositories.index_state import StageAction

from .conftest import (
    MemoryAcls,
    MemoryDocChunks,
    MemoryDocuments,
    MemoryDocumentVectors,
    MemoryIndexStates,
    document,
)


class _FakeOpenAI:
    def __init__(self) -> None:
        self.chat_calls: list[dict[str, object]] = []
        self.embedding_inputs: list[list[str]] = []
        self.chat = SimpleNamespace(completions=self)
        self.embeddings = self

    async def create(self, **kwargs):
        if "messages" in kwargs:
            self.chat_calls.append(kwargs)
            content = json.dumps(
                {
                    "contextual_prefix": "检索上下文",
                    "key_terms": ["术语", "术语", "检索"],
                }
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        inputs = list(kwargs["input"])
        self.embedding_inputs.append(inputs)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(index), 1.0]) for index in range(len(inputs))]
        )


def _acl(resource_id: str) -> ResourceAcl:
    return ResourceAcl(resource_id=resource_id, acl_revision=1, owner_id="owner")


async def _prepared_builder():
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    vectors = MemoryDocumentVectors()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=vectors,
        index_states=states,
    )
    preparer = DocumentPreparer(publication=publication, doc_chunks=chunks)
    markdown = "# 标题\n\n" + ("正文内容。" * 200)
    assert await preparer.prepare(
        resource_id="resource",
        document_version=1,
        markdown=markdown,
    ) is StageAction.STAGED
    revision = ContentRevision.create(
        resource_id="resource",
        document_version=1,
        raw_content=markdown,
    )
    client = _FakeOpenAI()
    builder = DocumentIndexBuilder(
        documents=documents,
        doc_chunks=chunks,
        resource_acls=MemoryAcls({"resource": _acl("resource")}),
        index_states=states,
        publication=publication,
        document_vectors=vectors,
        openai_client=client,
        query_model="query-model",
        embedding_model="embedding-model",
        embedding_dimensions=2,
        max_concurrency=5,
    )
    return builder, client, documents, chunks, states, vectors, revision


@pytest.mark.asyncio
async def test_index_builder_enhances_indexes_and_only_then_publishes() -> None:
    builder, client, _, chunks, states, vectors, revision = await _prepared_builder()

    await builder.build_and_publish(revision)

    saved = await chunks.get_revision_chunks(
        resource_id="resource",
        content_revision=revision.content_revision,
    )
    assert states.states["resource"].applied_content_revision == revision.content_revision
    assert all(chunk.contextual_prefix == "检索上下文" for chunk in saved)
    assert all(chunk.key_terms == ("术语", "检索") for chunk in saved)
    assert vectors.write_calls == 1
    assert client.embedding_inputs == [[chunk.get_semantic_text() for chunk in saved]]
    assert len(client.chat_calls) == len(saved)
    messages = client.chat_calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["content"].index("<shared_window>") < messages[1][
        "content"
    ].index("<target_chunk>")

    # 已发布 revision 是幂等 no-op，不能再次付出模型和 embedding 成本。
    await builder.build_and_publish(revision)
    assert len(client.chat_calls) == len(saved)
    assert len(client.embedding_inputs) == 1


@pytest.mark.asyncio
async def test_index_builder_rejects_missing_acl_before_model_calls() -> None:
    builder, client, documents, chunks, states, vectors, revision = await _prepared_builder()
    builder = DocumentIndexBuilder(
        documents=documents,
        doc_chunks=chunks,
        resource_acls=MemoryAcls({}),
        index_states=states,
        publication=DocumentPublication(
            documents=documents,
            doc_chunks=chunks,
            document_vectors=vectors,
            index_states=states,
        ),
        document_vectors=vectors,
        openai_client=client,
        query_model="query-model",
        embedding_model="embedding-model",
        embedding_dimensions=2,
        max_concurrency=5,
    )

    with pytest.raises(ValueError, match="ACL"):
        await builder.build_and_publish(revision)

    assert client.chat_calls == []
    assert client.embedding_inputs == []
    assert states.states["resource"].applied_content_revision is None


def test_shared_window_prefers_short_section_and_keeps_neighbor_chunks_complete() -> None:
    short_document = document(
        resource_id="resource",
        version=1,
        section_id="rsec",
        raw_content="短 Section 正文",
    )
    short_chunk = _chunk(section_id="rsec", index=0, text="目标 Chunk")
    assert _shared_window(short_document, [short_chunk], short_chunk) == (
        "标题路径: 标题\n\n短 Section 正文"
    )

    long_document = document(
        resource_id="resource",
        version=1,
        section_id="rsec",
        raw_content="长正文" * 4_000,
    )
    long_chunks = [
        _chunk(section_id="rsec", index=index, text=str(index) * 800)
        for index in range(5)
    ]
    window = _shared_window(long_document, long_chunks, long_chunks[2])
    assert all(str(index) * 800 in window for index in range(5))

    flat_chunk = _chunk(section_id=None, index=0, text="flat target")
    assert _shared_window(short_document, [flat_chunk], flat_chunk).endswith(
        "flat target"
    )


def _chunk(*, section_id: str | None, index: int, text: str) -> DocChunk:
    revision = "resource@1#hash"
    return DocChunk(
        chunk_id=rag_chunk_id(
            resource_id="resource",
            content_revision=revision,
            common_chunk_id=f"chunk:{index}",
        ),
        resource_id="resource",
        content_revision=revision,
        chunk_index=index,
        section_id=section_id,
        section_path=("标题",) if section_id is not None else (),
        raw_text=text,
        source_spans=(SourceSpan(0, len(text)),),
    )
