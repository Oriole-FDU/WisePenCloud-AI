"""发布测试显式装配内容与状态依赖；fake 投影不进入生产代码。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.markdown import MarkdownChunker as CommonMarkdownChunker

from rag.application.publication import DocumentPublication
from rag.core.schema import ContentRevision, Document, DocumentStructure
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
        revision = ContentRevision.create(
            resource_id=resource_id, document_version=version, text=text
        )
        result = CommonMarkdownChunker().chunk(text)
        return Document(
            resource_id,
            version,
            revision.content_revision,
            text,
            DocumentStructure(len(text), result.sections, result.anchors),
            metadata if metadata is not None else {"custom": True},
        )

    return make


@pytest.fixture
def projection():
    return SimpleNamespace(index=AsyncMock())


@pytest.fixture
def publication(storage, publication_store, projection):
    return DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(writer=storage), projection],
        ),
    )
