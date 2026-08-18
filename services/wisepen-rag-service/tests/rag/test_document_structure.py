import pytest

from common.utils.document import OutlineAssembler, SourceSpan
from rag.application.rag.index.constructor import build_document_structure
from rag.domain.models.structure import StructureMode


def test_source_span_uses_python_character_offsets() -> None:
    markdown = "甲🙂乙"
    span = SourceSpan(start_offset=1, end_offset=2)

    assert markdown[span.start_offset : span.end_offset] == "🙂"
    assert SourceSpan(1, 1) == SourceSpan(1, 1)


@pytest.mark.parametrize(("start_offset", "end_offset"), [(-1, 1), (2, 1)])
def test_source_span_rejects_invalid_half_open_ranges(
    start_offset: int,
    end_offset: int,
) -> None:
    with pytest.raises(ValueError):
        SourceSpan(start_offset, end_offset)


def test_nested_headings_build_parent_paths_and_subtree_ranges() -> None:
    markdown = "前言。\n\n# A\n\nA 正文。\n\n### C\n\nC 正文。\n\n## B\n\nB 正文。"
    structure = build_document_structure(markdown)

    root, section_a, section_c, section_b = structure.sections
    assert structure.mode is StructureMode.SECTIONED
    assert markdown[root.own_span.start_offset : root.own_span.end_offset].startswith(
        "前言。"
    )
    assert section_a.section_path == ("A",)
    assert section_c.section_path == ("A", "C")
    assert section_b.section_path == ("A", "B")
    assert section_a.parent_section_id == root.section_id
    assert section_c.parent_section_id == section_a.section_id
    assert section_b.parent_section_id == section_a.section_id
    assert section_c.ordinal == 0
    assert section_b.ordinal == 1
    assert section_c.subtree_span.end_offset == section_b.own_span.start_offset
    assert section_a.subtree_span.end_offset == len(markdown)


def test_duplicate_section_paths_keep_distinct_ids_and_outline_nodes() -> None:
    structure = build_document_structure("# 重复\n\nA\n\n# 重复\n\nB")
    outline = OutlineAssembler().assemble(
        sections=structure.sections,
        pages=structure.pages,
        anchors=structure.anchors,
    )

    assert len(outline) == 2
    assert outline[0].title == outline[1].title == "重复"
    assert outline[0].section_id != outline[1].section_id


def test_document_without_headings_is_flat_text() -> None:
    structure = build_document_structure("只有正文，没有标题。")

    assert structure.mode is StructureMode.FLAT_TEXT
    assert [section.title for section in structure.sections] == ["全文片段 1"]
    assert structure.sections[0].own_span == SourceSpan(0, len("只有正文，没有标题。"))


def test_section_ids_are_stable_and_do_not_depend_on_resource() -> None:
    markdown = "# 标题\n\n正文。"
    first = build_document_structure(markdown)
    repeated = build_document_structure(markdown)

    assert [section.section_id for section in first.sections] == [
        section.section_id for section in repeated.sections
    ]


def test_empty_document_has_no_structure_facts() -> None:
    structure = build_document_structure("   \n")

    assert structure.mode is StructureMode.EMPTY
    assert structure.total_length == 4
    assert structure.sections == []


def test_page_markers_keep_golden_source_offsets() -> None:
    markdown = "前言🙂\n<!-- page 7 -->\n正文甲\n<!-- page 8 -->\n正文乙"
    structure = build_document_structure(markdown)

    page_7_start = markdown.index("<!-- page 7 -->")
    page_8_start = markdown.index("<!-- page 8 -->")
    assert [page.page_label for page in structure.pages] == ["7", "8"]
    assert structure.pages[0].source_span == SourceSpan(page_7_start, page_8_start)
    assert structure.pages[1].source_span == SourceSpan(page_8_start, len(markdown))


def test_numbered_table_becomes_document_anchor() -> None:
    markdown = "# 数据\n\nTable 1: 样例\n\n| 名称 |\n|---|\n| A |"
    structure = build_document_structure(markdown)

    assert [anchor.label for anchor in structure.anchors] == ["Table 1"]


def test_duplicate_page_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate page label: 1"):
        build_document_structure("<!-- page 1 -->\nA\n<!-- page 1 -->\nB")
