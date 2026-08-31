from itertools import pairwise

from rag.application.rag.index.constructor import (
    build_document_structure,
    build_reading_blocks,
)
from rag.domain.models.structure import StructureMode


def test_long_section_builds_multiple_ordered_reading_blocks() -> None:
    markdown = "# 长章节\n\n" + "段落内容🙂。" * 1800
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    section = next(item for item in structure.sections if item.title == "长章节")
    section_blocks = [
        block for block in blocks if section.section_id in block.section_ids
    ]

    assert len(section_blocks) > 1
    assert [block.ordinal for block in section_blocks] == list(range(len(section_blocks)))
    assert all(len(block.raw_text) <= 6000 for block in section_blocks)


def test_oversized_paragraph_tail_absorbs_following_block() -> None:
    markdown = "# 长章节\n\n" + "甲" * 6200 + "\n\n" + "乙" * 3000
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )

    assert [len(block.raw_text) for block in blocks] == [5000, 4202]
    assert blocks[1].raw_text == "甲" * 1700 + "\n\n" + "乙" * 3000


def test_rebalancing_maximizes_shorter_reading_block_first() -> None:
    markdown = (
        "# 文档\n\n"
        "## A\n\n" + "甲" * 3000 + "\n\n"
        "## B\n\n" + "乙" * 1200 + "\n\n"
        "## C\n\n" + "丙" * 1200 + "\n\n"
        "## D\n\n" + "丁" * 1200
    )
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    sections_by_title = {section.title: section for section in structure.sections}

    # 三种合法边界中，A | B+C+D 的短侧最长，优先于 A+B | C+D。
    assert [block.section_ids for block in blocks] == [
        [sections_by_title["A"].section_id],
        [
            sections_by_title["B"].section_id,
            sections_by_title["C"].section_id,
            sections_by_title["D"].section_id,
        ],
    ]


def test_flat_text_builds_non_overlapping_sections_and_parent_blocks() -> None:
    markdown = "<!-- page 1 -->\n" + "无标题正文🙂。" * 2200
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )

    assert structure.mode is StructureMode.FLAT_TEXT
    assert len(structure.sections) == len(blocks) > 1
    assert all(len(block.raw_text) <= 6000 for block in blocks)
    assert all("<!-- page" not in block.raw_text for block in blocks)
    assert all(
        left.source_spans[-1].end_offset <= right.source_spans[0].start_offset
        for left, right in pairwise(blocks)
    )


def test_reading_block_keeps_cross_page_and_anchor_attribution() -> None:
    markdown = (
        "<!-- page 1 -->\n\n# 数据\n\n"
        "Table 1: 样例\n\n| 名称 |\n|---|\n| 甲🙂 |\n\n"
        "<!-- page 2 -->\n\n补充正文。"
    )
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    data_section = next(item for item in structure.sections if item.title == "数据")
    block = next(item for item in blocks if data_section.section_id in item.section_ids)

    assert block.page_labels == ["1", "2"]
    assert block.anchor_labels == ["Table 1"]
    assert "甲🙂" in block.raw_text
    assert "<!-- page" not in block.raw_text


def test_heading_without_direct_body_has_no_reading_block() -> None:
    markdown = "# 父标题\n\n## 子标题\n\n子正文。"
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    parent = next(item for item in structure.sections if item.title == "父标题")
    child = next(item for item in structure.sections if item.title == "子标题")

    assert [block for block in blocks if parent.section_id in block.section_ids] == []
    assert len([block for block in blocks if child.section_id in block.section_ids]) == 1


def test_adjacent_short_sibling_sections_share_one_reading_block() -> None:
    markdown = "# 文档\n\n## 摘要\n\n短摘要。\n\n## 结论\n\n短结论。"
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    summary = next(item for item in structure.sections if item.title == "摘要")
    conclusion = next(item for item in structure.sections if item.title == "结论")

    shared = next(block for block in blocks if summary.section_id in block.section_ids)

    assert shared.section_ids == [summary.section_id, conclusion.section_id]
    assert "短摘要。" in shared.raw_text
    assert "短结论。" in shared.raw_text


def test_empty_leaf_sibling_does_not_block_reading_block_merge() -> None:
    markdown = (
        "# 文档\n\n"
        "## 前文\n\n前文。\n\n"
        "## 空叶子\n\n"
        "## 当前\n\n当前。\n\n"
        "## 后文\n\n后文。"
    )
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    sections_by_title = {section.title: section for section in structure.sections}

    shared = next(
        block
        for block in blocks
        if sections_by_title["前文"].section_id in block.section_ids
    )

    assert shared.section_ids == [
        sections_by_title["前文"].section_id,
        sections_by_title["当前"].section_id,
        sections_by_title["后文"].section_id,
    ]


def test_empty_container_sibling_blocks_merge_across_its_subtree() -> None:
    markdown = (
        "# 文档\n\n"
        "## 前文\n\n前文。\n\n"
        "## 空容器\n\n"
        "### 空子节点\n\n"
        "## 当前\n\n当前。\n\n"
        "## 后文\n\n后文。"
    )
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    sections_by_title = {section.title: section for section in structure.sections}

    assert [
        sections_by_title["前文"].section_id,
    ] == next(
        block.section_ids
        for block in blocks
        if sections_by_title["前文"].section_id in block.section_ids
    )
    trailing = next(
        block
        for block in blocks
        if sections_by_title["当前"].section_id in block.section_ids
    )
    assert trailing.section_ids == [
        sections_by_title["当前"].section_id,
        sections_by_title["后文"].section_id,
    ]


def test_reading_blocks_never_cross_parent_section_boundary() -> None:
    markdown = "# A\n\n## A1\n\nA 内容。\n\n# B\n\n## B1\n\nB 内容。"
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    sections_by_id = {section.section_id: section for section in structure.sections}

    assert all(
        len({sections_by_id[item].parent_section_id for item in block.section_ids}) == 1
        for block in blocks
    )


def test_empty_document_builds_no_reading_blocks() -> None:
    structure = build_document_structure("")

    assert build_reading_blocks(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown="",
        structure=structure,
        sections=[],
    ) == []
