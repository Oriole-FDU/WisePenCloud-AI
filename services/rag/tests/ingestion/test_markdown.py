from dataclasses import fields, replace

import pytest
from common.utils.markdown import (
    MarkdownChunker as CommonMarkdownChunker,
)

from rag.core.schema import Document, DocumentStructure
from rag.ingestion import Chunk
from rag.ingestion.chunkers import MarkdownChunker


@pytest.fixture
def common_chunker():
    return CommonMarkdownChunker(40, 70)


@pytest.fixture
def document(common_chunker):
    markdown = (
        "文档开头的介绍。\n\n"
        "# 第一章\n\n第一章正文。\n\n"
        "## 数据\n\n表 1: 数据表\n\n| 名称 | 数值 |\n| --- | --- |\n| A | 1 |\n\n"
        "![图示](https://example.com/image.png)\n\n图 1: 数据图\n\n"
        "# 第二章\n\n第二章正文。\n"
    )
    result = common_chunker.chunk(markdown)
    return Document(
        resource_id="resource",
        document_version=5,
        content_revision="revision",
        raw_markdown=markdown,
        structure=DocumentStructure(len(markdown), result.sections, result.anchors),
        metadata={"custom": "文档 metadata 不复制到 Chunk"},
    )


async def test_markdown_chunker_adapts_common_fields_without_internal_details(
    document, common_chunker
):
    result = common_chunker.chunk(document.raw_markdown)
    adapter = MarkdownChunker(40, 70)

    chunks = await adapter.split(document)

    assert len(chunks) == len(result.chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert any(chunk.anchor_labels for chunk in chunks)
    assert any(len(chunk.section_path) == 2 for chunk in chunks)
    assert {item.name for item in fields(Chunk)} == {
        "chunk_id",
        "resource_id",
        "content_revision",
        "chunk_index",
        "section_id",
        "section_path",
        "raw_text",
        "content_token_count",
        "anchor_labels",
        "metadata",
    }
    for chunk, source in zip(chunks, result.chunks, strict=True):
        assert chunk.resource_id == document.resource_id
        assert chunk.content_revision == document.content_revision
        assert chunk.chunk_id == f"resource:revision:{source.chunk_id}"
        assert chunk.section_id == f"resource:revision:{source.section_id}"
        assert chunk.section_path == source.section_path
        assert chunk.raw_text == source.text
        assert chunk.content_token_count == source.content_token_count
        assert chunk.anchor_labels == source.anchor_labels
        assert chunk.metadata == {}
        assert not hasattr(chunk, "source_spans")
        assert not hasattr(chunk, "node_ids")
        assert not hasattr(chunk, "overflow")
        assert not hasattr(chunk, "content_hash")


async def test_markdown_identity_is_stable_and_scoped_to_resource_revision(document):
    adapter = MarkdownChunker(40, 70)
    first = await adapter.split(document)
    repeated = await adapter.split(document)
    new_version = await adapter.split(replace(document, document_version=99))
    new_resource = await adapter.split(replace(document, resource_id="other-resource"))
    new_revision = await adapter.split(
        replace(document, content_revision="other-revision")
    )

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in repeated]
    assert [chunk.section_id for chunk in first] == [
        chunk.section_id for chunk in repeated
    ]
    assert [chunk.chunk_id for chunk in first] == [
        chunk.chunk_id for chunk in new_version
    ]
    for original, resource, revision in zip(
        first, new_resource, new_revision, strict=True
    ):
        assert len({original.chunk_id, resource.chunk_id, revision.chunk_id}) == 3
        assert len({original.section_id, resource.section_id, revision.section_id}) == 3
        assert original.raw_text == resource.raw_text == revision.raw_text


async def test_markdown_chunker_uses_overridden_token_budgets():
    # 单个小节含超长正文：默认预算装箱为 1 个 chunk，小预算覆盖后被拆分为多个
    markdown = "# 长章节\n\n" + "这是一段足够长的正文内容，用于验证分块预算是否生效。\n\n" * 20
    document = Document(
        "resource", 1, "revision", markdown, DocumentStructure(len(markdown))
    )

    configured = await MarkdownChunker(40, 70).split(document)
    default_chunks = await MarkdownChunker().split(document)

    assert len(configured) > 1
    assert len(configured) > len(default_chunks)


@pytest.mark.parametrize("markdown", ["无标题的普通正文。", "", "# 只有标题\n"])
async def test_markdown_handles_missing_sections_and_empty_content(markdown):
    document = Document(
        "resource", 1, "revision", markdown, DocumentStructure(len(markdown))
    )

    chunks = await MarkdownChunker().split(document)

    if markdown:
        # Common 对仅有标题的输入回退为原文段落，RAG 保留该行为。
        assert len(chunks) == 1
        assert chunks[0].section_id is None
        assert chunks[0].section_path == []
        assert chunks[0].raw_text == markdown
    else:
        assert chunks == []
