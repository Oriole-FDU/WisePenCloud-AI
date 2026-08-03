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


class MemoryContextIndexingRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    async def get_many(
            self,
            *,
            resource_id: str,
            keys: Sequence[str],
    ) -> Mapping[str, str]:
        return {
            key: self.values[(resource_id, key)]
            for key in keys
            if (resource_id, key) in self.values
        }

    async def set_many(
            self,
            *,
            resource_id: str,
            values: Mapping[str, str],
    ) -> None:
        self.values.update(
            ((resource_id, key), value)
            for key, value in values.items()
        )


def _projection() -> RagContentProjection:
    return RagSectionProjector().project(
        RagDocumentContent(
            resource_id="resource-1",
            document_version=1,
            markdown="# 反向传播\n\n梯度沿计算图从输出层传回输入层。",
        )
    )


@pytest.mark.asyncio
async def test_context_indexing_enriches_index_text_and_reuses_repository() -> None:
    client = FakeQueryClient(
        ['{"indexing_context":"该片段解释反向传播中的梯度传递。"}']
    )
    repository = MemoryContextIndexingRepository()
    service = ContextIndexingService(client=client, repository=repository)
    projection = _projection()

    first = await service.contextualize(projection)
    second = await service.contextualize(projection)

    assert first.retrieval_chunks[0].raw_text == projection.retrieval_chunks[0].raw_text
    assert first.retrieval_chunks[0].index_text.startswith(
        "Context: 该片段解释反向传播中的梯度传递。"
    )
    assert second.retrieval_chunks == first.retrieval_chunks
    assert first.sections[1].preview == "梯度沿计算图从输出层传回输入层。"
    assert second.sections == first.sections
    assert len(client.prompts) == 1
    assert len(repository.values) == 1
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
        client=FakeQueryClient(['{"wrong_field":"missing field"}']),
        repository=MemoryContextIndexingRepository(),
    )

    with pytest.raises(ContextIndexingError, match="chunk"):
        await service.contextualize(_projection())


@pytest.mark.asyncio
async def test_context_indexing_stores_successes_before_retrying_failed_chunk() -> None:
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
    repository = MemoryContextIndexingRepository()
    service = ContextIndexingService(
        client=FakeQueryClient(
            [
                '{"indexing_context":"反向传播中的梯度传递。"}',
                '{"wrong_field":"missing field"}',
            ]
        ),
        repository=repository,
    )

    with pytest.raises(ContextIndexingError):
        await service.contextualize(projection)

    assert tuple(repository.values.values()) == ("反向传播中的梯度传递。",)

    retry_client = FakeQueryClient(
        ['{"indexing_context":"链式法则在梯度计算中的作用。"}']
    )
    result = await ContextIndexingService(
        client=retry_client,
        repository=repository,
    ).contextualize(projection)

    assert len(retry_client.prompts) == 1
    assert len(result.retrieval_chunks) == 2
