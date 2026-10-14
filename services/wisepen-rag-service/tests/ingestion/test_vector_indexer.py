"""向量 Indexer 的只读输入、文本来源、按批完成与失败边界。"""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.ingestion.indexers import VectorIndexer


@pytest.fixture
def embedding_client():
    return SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(
            side_effect=lambda **kwargs: [[1.0, 0.0] for _ in kwargs["texts"]]
        ),
    )


@pytest.fixture
def vector_writer():
    return SimpleNamespace(upsert=AsyncMock())


@pytest.fixture
def indexer(embedding_client, vector_writer):
    return VectorIndexer(
        embedding_client=embedding_client,
        writer=vector_writer,
        batch_size=2,
    )


async def test_zero_chunks_has_no_embedding_or_storage_io(
    indexer, embedding_client, vector_writer
):
    await indexer.index([])
    embedding_client.embed.assert_not_awaited()
    vector_writer.upsert.assert_not_awaited()


async def test_provider_default_dimensions_are_used_without_indexer_override(
    indexer, embedding_client, vector_writer, chunk
):
    embedding_client.dimensions = None
    embedding_client.embed.side_effect = None
    embedding_client.embed.return_value = [[1.0, 0.0, 0.5]]
    await indexer.index([chunk])
    assert vector_writer.upsert.call_args.args[0][0].embedding == [1.0, 0.0, 0.5]


async def test_retrieval_text_and_identity_projection_preserve_input(
    indexer, embedding_client, vector_writer, chunk
):
    second = replace(chunk, chunk_id="other", raw_text="第二块", metadata={})
    chunks = [chunk, second]
    before = deepcopy(chunks)
    embedding_client.embed.side_effect = None
    embedding_client.embed.return_value = [[1.0, 0.0], [0.0, 1.0]]
    await indexer.index(chunks)
    assert chunks == before
    embedding_client.embed.assert_awaited_once_with(
        texts=["标题\n\n文档上下文\n\n  中文正文\n", "标题\n\n第二块"]
    )
    records = vector_writer.upsert.call_args.args[0]
    assert [record.record_id for record in records] == [chunk.chunk_id, second.chunk_id]
    assert [record.embedding for record in records] == [[1.0, 0.0], [0.0, 1.0]]
    assert [record.text for record in records] == [
        chunk.retrieval_text,
        second.retrieval_text,
    ]
    assert all(
        record.metadata == {"resource_id": "resource", "content_revision": "revision"}
        for record in records
    )


async def test_embedding_and_storage_alternate_by_batch(
    indexer, embedding_client, vector_writer, chunk
):
    calls = []

    async def embed(**kwargs):
        calls.append(("embed", len(kwargs["texts"])))
        return [[1.0, 0.0] for _ in kwargs["texts"]]

    async def upsert(records):
        calls.append(("write", len(records)))

    embedding_client.embed.side_effect = embed
    vector_writer.upsert.side_effect = upsert
    await indexer.index([replace(chunk, chunk_id=str(index)) for index in range(5)])
    assert calls == [
        ("embed", 2),
        ("write", 2),
        ("embed", 2),
        ("write", 2),
        ("embed", 1),
        ("write", 1),
    ]


@pytest.mark.parametrize("conflicting_identity", [False, True])
async def test_business_metadata_factory_adds_fields_without_replacing_chunk_identity(
    embedding_client, vector_writer, chunk, conflicting_identity
):
    additions = {"year": 2020, "employee": "李明"}
    if conflicting_identity:
        additions.update(resource_id="wrong", content_revision="wrong")
    before = deepcopy(chunk)
    callback = []

    def metadata_factory(current):
        callback.append(current)
        return additions

    await VectorIndexer(
        embedding_client=embedding_client,
        writer=vector_writer,
        metadata_factory=metadata_factory,
    ).index([chunk])
    assert callback == [chunk]
    record = vector_writer.upsert.call_args.args[0][0]
    assert record.metadata == {
        "year": 2020,
        "employee": "李明",
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
    }
    assert chunk == before
    assert additions.get("resource_id") == ("wrong" if conflicting_identity else None)


@pytest.mark.parametrize("failure", ["embedding", "writer"])
async def test_later_batch_failure_stops_without_compensating_completed_writes(
    indexer, embedding_client, vector_writer, chunk, failure
):
    error = OSError("provider failed")
    if failure == "embedding":
        embedding_client.embed.side_effect = [[[1.0, 0.0], [1.0, 0.0]], error]
    else:
        vector_writer.upsert.side_effect = [None, error]
    with pytest.raises(OSError) as caught:
        await indexer.index([replace(chunk, chunk_id=str(index)) for index in range(5)])
    assert caught.value is error
    assert embedding_client.embed.await_count == 2
    assert vector_writer.upsert.await_count == (1 if failure == "embedding" else 2)


@pytest.mark.parametrize(
    "response",
    [
        [],
        [[1.0]],
        [[float("nan"), 0.0]],
        [[float("inf"), 0.0]],
        [[True, 0.0]],
        [["1", 0.0]],
        [[1.0, 0.0], [0.0, 1.0]],
    ],
)
async def test_invalid_embedding_response_is_retryable_and_never_written(
    indexer, embedding_client, vector_writer, chunk, response
):
    embedding_client.embed.side_effect = None
    embedding_client.embed.return_value = response
    if len(response) != 1:
        embedding_client.embed.side_effect = ValueError(
            "embedding response count does not match texts"
        )
    with pytest.raises(RuntimeError) as caught:
        await indexer.index([chunk])
    assert isinstance(caught.value, RuntimeError)
    vector_writer.upsert.assert_not_awaited()


async def test_common_response_value_error_becomes_retryable(
    indexer, embedding_client, vector_writer, chunk
):
    original = ValueError("embedding response count does not match texts")
    embedding_client.embed.side_effect = original
    with pytest.raises(RuntimeError) as caught:
        await indexer.index([chunk])
    assert caught.value.__cause__ is original
    assert isinstance(caught.value, RuntimeError)
    vector_writer.upsert.assert_not_awaited()
