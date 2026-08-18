import json
from dataclasses import dataclass

import pytest

from rag.application.rag.index import ContextualTextIndexer
from rag.domain.models.content import ReadingBlock
from rag.domain.models.retrieval import RetrievalChunk
from rag.domain.models.structure import (
    DocumentStructure,
    Section,
    StructureMode,
)
from common.utils.document import SourceSpan


@dataclass
class _Response:
    content: str


class _Client:
    model = "test-model"
    thinking = "disabled"

    def __init__(self, content: str = '{"contextual_text": "topic context"}') -> None:
        self.content = content
        self.calls = 0

    async def aquery(self, prompt: str, **kwargs) -> _Response:
        self.calls += 1
        self.prompt = prompt
        self.kwargs = kwargs
        return _Response(self.content)


class _ArtifactStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.get_calls = []
        self.set_calls = []

    async def get_many(self, *, resource_id, artifact_kind, artifact_keys):
        self.get_calls.append((resource_id, artifact_kind, list(artifact_keys)))
        return {
            key: self.values[key]
            for key in artifact_keys
            if key in self.values
        }

    async def set_many(self, *, resource_id, artifact_kind, artifacts):
        self.set_calls.append((resource_id, artifact_kind, dict(artifacts)))
        self.values.update(artifacts)

    async def delete_resources(self, resource_ids):
        pass


def _inputs(mode: StructureMode = StructureMode.SECTIONED):
    section = Section(
        section_id="section-1",
        title="Title",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["Title"],
        own_span=SourceSpan(0, 20),
        subtree_span=SourceSpan(0, 20),
        preview="A short section preview.",
    )
    block = ReadingBlock(
        block_id="block-1",
        section_id="section-1",
        ordinal=0,
        raw_text="Target passage.",
        source_spans=[SourceSpan(0, 15)],
    )
    chunk = RetrievalChunk(
        chunk_id="chunk-1",
        reading_block_id="block-1",
        section_id="section-1",
        section_path=["Title"],
        raw_text="Target passage.",
        index_text="Target passage.",
        source_spans=[SourceSpan(0, 15)],
    )
    structure = DocumentStructure(
        mode=mode,
        total_length=20,
        sections=[section] if mode is StructureMode.SECTIONED else [],
    )
    return structure, [block], [chunk]


@pytest.mark.asyncio
async def test_contextual_text_generates_and_only_enhances_index_text() -> None:
    structure, blocks, chunks = _inputs()
    client = _Client()
    artifact_store = _ArtifactStore()

    result = await ContextualTextIndexer(client=client, artifact_store=artifact_store).contextualize(
        resource_id="resource-1",
        structure=structure,
        reading_blocks=blocks,
        chunks=chunks,
    )

    assert client.calls == 1
    assert result[0].raw_text == chunks[0].raw_text
    assert result[0].source_spans == chunks[0].source_spans
    assert result[0].index_text == "Context: topic context\n\nTarget passage."
    assert artifact_store.set_calls[0][0:2] == (
        "resource-1",
        "context",
    )
    assert "Title" in client.prompt
    assert "A short section preview." in client.prompt
    assert "Target passage." in client.prompt


@pytest.mark.asyncio
async def test_contextual_text_stored_artifact_skips_model() -> None:
    structure, blocks, chunks = _inputs()
    first_client = _Client()
    artifact_store = _ArtifactStore()
    first_result = await ContextualTextIndexer(
        client=first_client,
        artifact_store=artifact_store,
    ).contextualize(
        resource_id="resource-1",
        structure=structure,
        reading_blocks=blocks,
        chunks=chunks,
    )

    second_client = _Client('{"contextual_text": "should not be used"}')
    result = await ContextualTextIndexer(
        client=second_client,
        artifact_store=artifact_store,
    ).contextualize(
        resource_id="resource-1",
        structure=structure,
        reading_blocks=blocks,
        chunks=chunks,
    )

    assert first_result[0].index_text == result[0].index_text
    assert second_client.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StructureMode.FLAT_TEXT, StructureMode.EMPTY])
async def test_contextual_text_skips_flat_text_and_empty_revisions(mode) -> None:
    structure, blocks, chunks = _inputs(mode)
    client = _Client()
    artifact_store = _ArtifactStore()

    result = await ContextualTextIndexer(client=client, artifact_store=artifact_store).contextualize(
        resource_id="resource-1",
        structure=structure,
        reading_blocks=blocks,
        chunks=chunks,
    )

    assert result == chunks
    assert client.calls == 0
    assert artifact_store.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["not-json", '{"contextual_text": ""}'])
async def test_contextual_text_rejects_invalid_model_response(content: str) -> None:
    structure, blocks, chunks = _inputs()

    with pytest.raises((json.JSONDecodeError, ValueError)):
        await ContextualTextIndexer(
            client=_Client(content),
            artifact_store=_ArtifactStore(),
        ).contextualize(
            resource_id="resource-1",
            structure=structure,
            reading_blocks=blocks,
            chunks=chunks,
        )
