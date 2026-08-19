from itertools import pairwise

from rag.application.rag.index.constructor import (
    build_document_structure,
    build_reading_blocks,
    build_retrieval_chunks,
    build_source_refs,
)


def test_retrieval_chunks_have_stable_ids_and_exact_source_text() -> None:
    markdown = "# 标题\n\n" + "正文内容🙂。" * 400
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    original = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )
    repeated = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )

    assert len(original) > 1
    assert [chunk.chunk_id for chunk in original] == [chunk.chunk_id for chunk in repeated]
    assert all(chunk.index_text == chunk.raw_text for chunk in original)


def test_flat_text_retrieval_chunks_keep_100_character_overlap() -> None:
    markdown = "无标题内容🙂。" * 500
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    chunks = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )
    first_block_chunks = [
        chunk for chunk in chunks if chunk.reading_block_id == blocks[0].block_id
    ]

    assert len(first_block_chunks) > 1
    assert all(len(chunk.raw_text) <= 800 for chunk in first_block_chunks)
    overlaps = [
        left.source_spans[-1].end_offset - right.source_spans[0].start_offset
        for left, right in pairwise(first_block_chunks)
    ]
    assert all(0 < overlap <= 100 for overlap in overlaps)


def test_source_refs_preserve_reading_block_ownership() -> None:
    markdown = "<!-- page 1 -->\n\n# 数据\n\nTable 1: 样例\n\n| 值 |\n|---|\n| 甲 |"
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    chunks = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )
    refs = build_source_refs(
        resource_id="resource-1",
        content_revision="revision-1",
        retrieval_chunks=chunks,
    )

    assert len(refs) == len(chunks)
    assert refs[0].reading_block_id == chunks[0].reading_block_id
    assert refs[0].section_id == chunks[0].section_id
    assert refs[0].source_spans == chunks[0].source_spans
    assert refs[0].page_labels == ["1"]
    assert refs[0].anchor_labels == ["Table 1"]


def test_shared_reading_block_keeps_retrieval_chunks_in_one_section() -> None:
    markdown = "# 文档\n\n## 摘要\n\n摘要内容。\n\n## 结论\n\n结论内容。"
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    chunks = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )
    summary = next(item for item in structure.sections if item.title == "摘要")
    conclusion = next(item for item in structure.sections if item.title == "结论")
    shared = next(block for block in blocks if summary.section_id in block.section_ids)
    shared_chunks = [
        chunk for chunk in chunks if chunk.reading_block_id == shared.block_id
    ]

    assert {chunk.section_id for chunk in shared_chunks} == {
        summary.section_id,
        conclusion.section_id,
    }
    for chunk in shared_chunks:
        section = summary if chunk.section_id == summary.section_id else conclusion
        assert all(
            any(
                span.start_offset >= content_span.start_offset
                and span.end_offset <= content_span.end_offset
                for content_span in section.content_spans
            )
            for span in chunk.source_spans
        )


def test_empty_document_builds_no_retrieval_facts() -> None:
    structure = build_document_structure("")

    assert build_retrieval_chunks(
        markdown="",
        structure=structure,
        sections=[],
        reading_blocks=[],
    ) == []
    assert build_source_refs(
        resource_id="resource-1",
        content_revision="revision-1",
        retrieval_chunks=[],
    ) == []
