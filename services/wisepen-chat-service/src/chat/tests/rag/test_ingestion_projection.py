from __future__ import annotations

from chat.application.rag.ingestion import RagDocumentContent, RagSectionProjector


def test_empty_content_produces_only_document_root() -> None:
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 4, "")
    )

    assert projection.content_hash
    assert projection.reading_blocks == ()
    assert projection.retrieval_chunks == ()
    assert projection.source_refs == ()
    assert len(projection.sections) == 1
    assert projection.sections[0].section_path == ()
    assert projection.sections[0].own_start == 0
    assert projection.sections[0].own_end == 0


def test_paginated_projection_keeps_pages_inside_section_reading_blocks() -> None:
    markdown = "\n\n".join(
        (
            "<!-- page 1 -->",
            "# 产品",
            "产品导语。",
            "## 安装",
            "安装步骤。",
            "<!-- page 2 -->",
            "安装补充。",
            "## 配置",
            "配置说明。",
        )
    )
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 2, markdown)
    )

    install = next(
        section
        for section in projection.sections
        if section.section_path == ("产品", "安装")
    )
    install_blocks = tuple(
        block
        for block in projection.reading_blocks
        if block.section_id == install.section_id
    )
    assert [block.page_labels for block in install_blocks] == [("1",), ("2",)]
    assert all("<!-- page" not in block.raw_text for block in install_blocks)

    sections_by_id = {section.section_id: section for section in projection.sections}
    blocks_by_id = {block.block_id: block for block in projection.reading_blocks}
    refs_by_chunk = {ref.chunk_id: ref for ref in projection.source_refs}
    for chunk in projection.retrieval_chunks:
        block = blocks_by_id[chunk.reading_block_id]
        section = sections_by_id[chunk.section_id]
        source_ref = refs_by_chunk[chunk.chunk_id]
        assert block.section_id == chunk.section_id
        assert chunk.section_path == section.section_path
        assert source_ref.section_id == chunk.section_id
        assert all(
            section.own_start <= span.start_offset < span.end_offset <= section.own_end
            for span in chunk.source_spans
        )
        assert chunk.raw_text == "\n\n".join(
            markdown[span.start_offset : span.end_offset]
            for span in chunk.source_spans
        )


def test_long_section_has_multiple_reading_blocks_without_changing_section() -> None:
    markdown = "# 长章节\n\n" + "段落内容。" * 1400
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 1, markdown)
    )
    section = next(item for item in projection.sections if item.title == "长章节")
    blocks = tuple(
        block
        for block in projection.reading_blocks
        if block.section_id == section.section_id
    )

    assert len(blocks) > 1
    assert [block.ordinal for block in blocks] == list(range(len(blocks)))
    assert all(block.section_id == section.section_id for block in blocks)
    assert all(
        section.own_start <= span.start_offset < span.end_offset <= section.own_end
        for block in blocks
        for span in block.source_spans
    )
    assert all(
        chunk.section_id == section.section_id
        for chunk in projection.retrieval_chunks
    )
    assert len(projection.retrieval_chunks) > len(blocks)


def test_section_tree_handles_heading_jumps_and_preface() -> None:
    markdown = "前言。\n\n# A\n\nA 正文。\n\n### C\n\nC 正文。\n\n## B\n\nB 正文。"
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 1, markdown)
    )

    root, section_a, section_c, section_b = projection.sections
    assert markdown[root.own_start : root.own_end].startswith("前言。")
    assert section_a.parent_section_id == root.section_id
    assert section_a.ordinal == 0
    assert section_c.parent_section_id == section_a.section_id
    assert section_c.ordinal == 0
    assert section_b.parent_section_id == section_a.section_id
    assert section_b.ordinal == 1
    assert section_c.subtree_end == section_b.own_start
    assert section_a.subtree_end == len(markdown)


def test_source_ref_only_keeps_anchors_inside_its_section() -> None:
    markdown = "\n\n".join(
        (
            "<!-- page 1 -->",
            "# A",
            "Table 1: A\n\n| Name |\n|---|\n| A |",
            "## B",
            "| Name |\n|---|\n| B |\n\nTable 2: B",
        )
    )
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 1, markdown)
    )

    anchors_by_path = {
        ref.section_path: ref.anchor_labels for ref in projection.source_refs
    }
    assert anchors_by_path[("A",)] == ("Table 1",)
    assert anchors_by_path[("A", "B")] == ("Table 2",)


def test_projection_ids_are_resource_and_revision_scoped() -> None:
    projector = RagSectionProjector()
    markdown = "# 标题\n\n正文。"

    original = projector.project(RagDocumentContent("r1", 1, markdown))
    repeated = projector.project(RagDocumentContent("r1", 1, markdown))
    next_version = projector.project(RagDocumentContent("r1", 2, markdown))
    other_resource = projector.project(RagDocumentContent("r2", 1, markdown))

    assert original.reading_blocks[0].block_id == repeated.reading_blocks[0].block_id
    assert original.retrieval_chunks[0].chunk_id == repeated.retrieval_chunks[0].chunk_id
    assert original.source_refs[0].ref_id == repeated.source_refs[0].ref_id
    assert original.retrieval_chunks[0].chunk_id != next_version.retrieval_chunks[0].chunk_id
    assert original.retrieval_chunks[0].chunk_id != other_resource.retrieval_chunks[0].chunk_id
