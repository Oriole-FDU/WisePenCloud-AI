from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.domain.models import Chunk, Document, DocumentStructure
from rag.ingestion import IngestionPipeline


@pytest.fixture
def document():
    return Document("resource", 1, "revision", "正文", DocumentStructure(2))


@pytest.fixture
def chunks():
    return [Chunk("chunk", "resource", "revision", 0, None, [], "正文", 2, [])]


async def test_pipeline_preserves_order_identity_and_in_place_metadata(
        document, chunks
):
    calls = []
    identity = (chunks[0].chunk_id, chunks[0].content_revision, chunks[0].chunk_index)

    async def split(current_document):
        assert current_document is document
        calls.append("chunker")
        return chunks

    async def transform_first(current_chunks, *, document=None):
        assert current_chunks is chunks
        calls.append("transformer-1")
        current_chunks[0].metadata["contextual_context"] = "上下文"
        return current_chunks

    async def transform_second(current_chunks, *, document=None):
        assert current_chunks is chunks
        assert current_chunks[0].metadata == {"contextual_context": "上下文"}
        calls.append("transformer-2")
        current_chunks[0].metadata["custom"] = {"enabled": True}
        return current_chunks

    async def index_first(current_chunks):
        assert current_chunks is chunks
        assert current_chunks[0].metadata["custom"] == {"enabled": True}
        calls.append("indexer-1")

    async def index_second(current_chunks):
        assert current_chunks is chunks
        assert calls[-1] == "indexer-1"
        calls.append("indexer-2")

    chunker = SimpleNamespace(split=AsyncMock(side_effect=split))
    transformers = [
        SimpleNamespace(transform=AsyncMock(side_effect=transform_first)),
        SimpleNamespace(transform=AsyncMock(side_effect=transform_second)),
    ]
    indexers = [
        SimpleNamespace(index=AsyncMock(side_effect=index_first)),
        SimpleNamespace(index=AsyncMock(side_effect=index_second)),
    ]
    pipeline = IngestionPipeline(
        chunker=chunker, transformers=transformers, indexers=indexers
    )

    assert await pipeline.run(document) is chunks
    assert calls == [
        "chunker",
        "transformer-1",
        "transformer-2",
        "indexer-1",
        "indexer-2",
    ]
    chunker.split.assert_awaited_once_with(document)
    for transformer in transformers:
        transformer.transform.assert_awaited_once_with(chunks, document=document)
    assert (
               chunks[0].chunk_id,
               chunks[0].content_revision,
               chunks[0].chunk_index,
           ) == identity


async def test_pipeline_passes_each_returned_list_and_indexes_only_final_list(
        document, chunks
):
    intermediate = [chunks[0]]
    final = [Chunk("new-chunk", "resource", "revision", 1, None, [], "新正文", 3, [])]
    chunker = SimpleNamespace(split=AsyncMock(return_value=chunks))
    first = SimpleNamespace(transform=AsyncMock(return_value=intermediate))
    second = SimpleNamespace(transform=AsyncMock(return_value=final))
    indexers = [SimpleNamespace(index=AsyncMock()), SimpleNamespace(index=AsyncMock())]
    pipeline = IngestionPipeline(
        chunker=chunker, transformers=[first, second], indexers=indexers
    )

    assert await pipeline.run(document) is final

    assert first.transform.call_args.args[0] is chunks
    assert second.transform.call_args.args[0] is intermediate
    assert first.transform.call_args.kwargs["document"] is document
    assert second.transform.call_args.kwargs["document"] is document
    for indexer in indexers:
        indexer.index.assert_awaited_once_with(final)
        assert indexer.index.call_args.args[0] is final


@pytest.mark.parametrize("failed_stage", range(5))
async def test_pipeline_propagates_original_failure_and_stops(
        document, chunks, failed_stage
):
    calls = []
    writes = []
    error = RuntimeError("阶段失败")

    async def execute(stage, current_chunks):
        calls.append(stage)
        if stage == failed_stage:
            raise error
        if stage >= 3:
            writes.append(stage)
        return current_chunks

    async def split(current_document):
        return await execute(0, chunks)

    async def first_transform(current_chunks, *, document=None):
        return await execute(1, current_chunks)

    async def second_transform(current_chunks, *, document=None):
        return await execute(2, current_chunks)

    async def first_index(current_chunks):
        await execute(3, current_chunks)

    async def second_index(current_chunks):
        await execute(4, current_chunks)

    pipeline = IngestionPipeline(
        chunker=SimpleNamespace(split=split),
        transformers=[
            SimpleNamespace(transform=first_transform),
            SimpleNamespace(transform=second_transform),
        ],
        indexers=[
            SimpleNamespace(index=first_index),
            SimpleNamespace(index=second_index),
        ],
    )

    with pytest.raises(RuntimeError) as caught:
        await pipeline.run(document)

    assert caught.value is error
    assert calls == list(range(failed_stage + 1))
    # 后一个 Indexer 失败时，前一个已完成的写入保留，不执行补偿。
    assert writes == ([3] if failed_stage == 4 else [])


@pytest.mark.parametrize("empty", [False, True])
async def test_pipeline_without_transformers_passes_chunker_list(
        document, chunks, empty
):
    initial = [] if empty else chunks
    chunker = SimpleNamespace(split=AsyncMock(return_value=initial))
    indexer = SimpleNamespace(index=AsyncMock())
    pipeline = IngestionPipeline(chunker=chunker, transformers=[], indexers=[indexer])

    assert await pipeline.run(document) is initial

    chunker.split.assert_awaited_once_with(document)
    assert indexer.index.call_args.args[0] is initial


async def test_pipeline_with_no_indexers_still_runs_transformers(document, chunks):
    chunker = SimpleNamespace(split=AsyncMock(return_value=chunks))
    transformer = SimpleNamespace(transform=AsyncMock(return_value=[]))
    pipeline = IngestionPipeline(
        chunker=chunker, transformers=[transformer], indexers=[]
    )

    assert await pipeline.run(document) == []
    transformer.transform.assert_awaited_once_with(chunks, document=document)
