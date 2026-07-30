from __future__ import annotations

import pytest

from rag.application.rag.ingestion import (
    RagContentIndexResult,
    RagDocumentContent,
    RagProjectionStage,
    RagProjectionStageAction,
)
from rag.application.rag.kafka_consumers import (
    DocumentReadyMessageError,
    RagDocumentReadyConsumer,
)


class _RecordingContentIndexer:
    def __init__(self) -> None:
        self.contents: list[RagDocumentContent] = []

    async def index(self, content: RagDocumentContent) -> RagContentIndexResult:
        self.contents.append(content)
        return RagContentIndexResult(
            stage=RagProjectionStage(
                resource_id=content.resource_id,
                document_version=content.document_version,
                content_revision="revision-1",
                action=RagProjectionStageAction.STAGED,
            ),
            indexed_chunk_count=1,
        )


class _GraphIndexer:
    async def index(self, **kwargs):
        raise AssertionError("consumer.index must not run graph projection")


def _consumer(indexer: _RecordingContentIndexer) -> RagDocumentReadyConsumer:
    return RagDocumentReadyConsumer(
        content_indexer=indexer,
        graph_indexer=_GraphIndexer(),
    )


@pytest.mark.asyncio
async def test_document_ready_consumer_passes_message_to_content_indexer() -> None:
    indexer = _RecordingContentIndexer()
    consumer = _consumer(indexer)

    result = await consumer.index(
        {
            "resourceId": " resource-1 ",
            "version": 2,
            "content": "<!-- page 1 -->\n\n正文。",
        }
    )

    assert result.stage.document_version == 2
    assert indexer.contents[0].resource_id == "resource-1"
    assert indexer.contents[0].markdown == "<!-- page 1 -->\n\n正文。"


@pytest.mark.parametrize(
    "payload",
    (
        {"resourceId": "", "version": 1, "content": "正文"},
        {"resourceId": "r1", "version": "1", "content": "正文"},
        {"resourceId": "r1", "version": True, "content": "正文"},
        {"resourceId": "r1", "version": 0, "content": "正文"},
        {"resourceId": "r1", "version": 1, "content": None},
    ),
)
@pytest.mark.asyncio
async def test_document_ready_consumer_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DocumentReadyMessageError):
        await _consumer(_RecordingContentIndexer()).index(payload)
