from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rag.application.rag.ingestion import (
    ContextIndexingError,
    ContextIndexingService,
    RagContentProjection,
    RagDocumentContent,
    RagSectionProjector,
)


class FakeQueryClient:
    model = "context-model"
    thinking = "disabled"

    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def aquery(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.responses.pop(0))


class MemoryContextIndexingCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        return {key: self.values[key] for key in keys if key in self.values}

    async def set_many(self, values: Mapping[str, str]) -> None:
        self.values.update(values)


def _projection() -> RagContentProjection:
    return RagSectionProjector().project(
        RagDocumentContent(
            resource_id="resource-1",
            document_version=1,
            markdown="# 反向传播\n\n梯度沿计算图从输出层传回输入层。",
        )
    )


@pytest.mark.asyncio
async def test_context_indexing_enriches_index_text_and_reuses_cache() -> None:
    client = FakeQueryClient(
        ['{"indexing_context":"该片段解释反向传播中的梯度传递。"}']
    )
    cache = MemoryContextIndexingCache()
    service = ContextIndexingService(client=client, cache=cache)
    projection = _projection()

    first = await service.contextualize(projection)
    second = await service.contextualize(projection)

    assert first.retrieval_chunks[0].raw_text == projection.retrieval_chunks[0].raw_text
    assert first.retrieval_chunks[0].index_text.startswith(
        "Context: 该片段解释反向传播中的梯度传递。"
    )
    assert second.retrieval_chunks == first.retrieval_chunks
    assert first.sections[1].summary == "该片段解释反向传播中的梯度传递。"
    assert len(client.prompts) == 1
    assert len(cache.values) == 1
    prompt = client.prompts[0]
    assert "<section_path>" in prompt
    assert "<section_reading_block>" in prompt
    assert "<target_retrieval_chunk>" in prompt
    assert "<![CDATA[" in prompt
    assert "Verbatim text that will be retrieved" in prompt
    assert "反向传播" in prompt


@pytest.mark.asyncio
async def test_context_indexing_rejects_invalid_llm_output() -> None:
    service = ContextIndexingService(
        client=FakeQueryClient(['{"summary":"missing field"}']),
        cache=MemoryContextIndexingCache(),
    )

    with pytest.raises(ContextIndexingError, match="chunk"):
        await service.contextualize(_projection())


@pytest.mark.asyncio
async def test_context_indexing_caches_successes_before_retrying_failed_chunk() -> None:
    projection = _projection()
    first_chunk = projection.retrieval_chunks[0]
    second_text = "链式法则给出复合函数的梯度。"
    projection = replace(
        projection,
        retrieval_chunks=(
            first_chunk,
            replace(
                first_chunk,
                chunk_id="chunk-2",
                raw_text=second_text,
                index_text=second_text,
            ),
        ),
    )
    cache = MemoryContextIndexingCache()
    service = ContextIndexingService(
        client=FakeQueryClient(
            [
                '{"indexing_context":"反向传播中的梯度传递。"}',
                '{"summary":"missing field"}',
            ]
        ),
        cache=cache,
    )

    with pytest.raises(ContextIndexingError):
        await service.contextualize(projection)

    assert tuple(cache.values.values()) == ("反向传播中的梯度传递。",)

    retry_client = FakeQueryClient(
        ['{"indexing_context":"链式法则在梯度计算中的作用。"}']
    )
    result = await ContextIndexingService(
        client=retry_client,
        cache=cache,
    ).contextualize(projection)

    assert len(retry_client.prompts) == 1
    assert len(result.retrieval_chunks) == 2
