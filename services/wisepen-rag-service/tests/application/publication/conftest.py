"""发布测试显式装配内容与状态依赖；fake 投影不进入生产代码。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.publication import DocumentPublication, DocumentReadyEvent
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.pipeline import IngestionPipeline


@pytest.fixture
def make_document():
    def make(
        *,
        version=1,
        text="# 标题\n\n  中文正文😀。\n\n",
        resource_id="resource",
        metadata=None,
    ):
        document = DocumentReadyEvent.model_validate(
            {"resourceId": resource_id, "version": version, "content": text}
        ).to_document()
        document.metadata = metadata if metadata is not None else {"custom": True}
        return document

    return make


@pytest.fixture
def projection():
    return SimpleNamespace(index=AsyncMock())


@pytest.fixture
def pipeline_routes():
    return {}


@pytest.fixture
def publication(storage, publication_store, projection, pipeline_routes):
    return DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(writer=storage), projection],
        ),
        pipelines=pipeline_routes,
    )
