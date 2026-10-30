from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.publication.ingestion import ChunkFactsIndexer


async def test_chunk_facts_indexer_only_writes_shared_input(chunk):
    writer = SimpleNamespace(upsert_chunks=AsyncMock(), upsert_documents=AsyncMock())
    chunks = [chunk]
    metadata = chunk.metadata
    before = dict(metadata)

    assert await ChunkFactsIndexer(writer).index(chunks) is None

    writer.upsert_chunks.assert_awaited_once_with(chunks)
    assert writer.upsert_chunks.call_args.args[0] is chunks
    assert chunks[0] is chunk
    assert chunk.metadata is metadata
    assert chunk.metadata == before
    writer.upsert_documents.assert_not_awaited()


async def test_chunk_facts_indexer_propagates_original_writer_error(chunk):
    error = RuntimeError("内容写入失败")
    writer = SimpleNamespace(upsert_chunks=AsyncMock(side_effect=error))

    with pytest.raises(RuntimeError) as caught:
        await ChunkFactsIndexer(writer).index([chunk])

    assert caught.value is error
    assert writer.upsert_chunks.await_count == 1
