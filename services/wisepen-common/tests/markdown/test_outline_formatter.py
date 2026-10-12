from dataclasses import replace

from common.utils.markdown import MarkdownChunker, OutlineFormatter


def test_clean_outline_and_neighborhood_only_keep_headings():
    result = MarkdownChunker().chunk("# Alpha\n\nBody\n\n## Child\n\nChild body\n")
    sections = [
        replace(section, summary="Summary", preview="Preview")
        for section in result.sections
    ]
    child = next(section for section in sections if section.title == "Child")
    formatter = OutlineFormatter(
        sections=sections, anchors=result.anchors, show_extra_info=False
    )
    assert formatter.global_outline() == "# Alpha\n## Child"
    assert formatter.neighborhood(child.section_id) == "# Alpha\n## Child"
    assert "id=" in OutlineFormatter(sections=sections).global_outline()


def test_global_outline_formats_tree_depth_ids_offsets_and_anchor_labels() -> None:
    source = (
        "# Alpha\n\nAlpha direct text.\n\n"
        "## Child\n\nTable 1: Measurements\n\n"
        "| Value |\n| --- |\n| 42 |\n\n"
        "### Grandchild\n\nNested text.\n\n"
        "## Sibling\n\nSibling text.\n\n"
        "# Beta\n\nBeta text.\n"
    )
    result = MarkdownChunker().chunk(source)
    formatter = OutlineFormatter(
        sections=result.sections,
        anchors=result.anchors,
    )

    outline = formatter.global_outline(max_level=2)

    assert "# Alpha id=sec_" in outline
    assert "## Child " in outline
    assert "## Sibling id=sec_" in outline
    assert "Grandchild" not in outline
    assert "[Table 1]" in outline
    assert "@" in outline
    assert "{#" not in outline
    assert "chars" not in outline
    assert formatter.global_outline(max_level=1).count("\n") == 3


def test_neighborhood_keeps_ancestors_marks_current_and_limits_sibling_window() -> None:
    result = MarkdownChunker().chunk(
        "# Alpha\n\nAlpha text.\n\n"
        "## Before\n\nBefore text.\n\n"
        "## Current\n\nCurrent text.\n\n"
        "### Direct child\n\nChild text.\n\n"
        "#### Deep child\n\nDeep text.\n\n"
        "## After\n\nAfter text.\n\n"
        "## Outside\n\nOutside text.\n"
    )
    current = next(section for section in result.sections if section.title == "Current")
    formatter = OutlineFormatter(
        sections=result.sections,
        anchors=result.anchors,
    )

    outline = formatter.neighborhood(current.section_id, sibling_steps=1)

    assert outline.index("# Alpha id=") < outline.index("## Current [C] id=")
    assert "## Current [C] id=" in outline
    assert "### Direct child id=" in outline
    assert "Deep child" not in outline
    assert "Before" in outline and "After" in outline
    assert "Outside" not in outline


def test_document_title_is_kept_distinct_from_document_start() -> None:
    result = MarkdownChunker().chunk("# Alpha\n\nBody\n", title="Report")
    outline = OutlineFormatter(
        sections=result.sections,
        anchors=result.anchors,
    ).global_outline()

    assert outline.splitlines()[0] == (
        f"Report<文档开头> id={result.sections[0].section_id} @0"
    )
    assert outline.splitlines()[1].startswith("# Alpha id=")


def test_node_line_prefers_summary_over_preview() -> None:
    result = MarkdownChunker().chunk(
        "# Alpha\n\nAlpha text.\n\n## Beta\n\nBeta preview\n"
    )
    # frozen dataclass：用 replace 模拟 LLM 生成的章节摘要
    sections = [
        replace(section, summary="Alpha 摘要" if section.title == "Alpha" else "")
        for section in result.sections
    ]
    outline = OutlineFormatter(
        sections=sections, anchors=result.anchors
    ).global_outline()

    lines = outline.splitlines()
    alpha_index = next(
        index for index, line in enumerate(lines) if line.startswith("# Alpha")
    )
    assert lines[alpha_index + 1] == "[Summary]: Alpha 摘要"
    beta_line = next(line for line in lines if line.startswith("## Beta"))
    beta_index = lines.index(beta_line)
    assert lines[beta_index + 1] == "[Preview]: Beta preview"


def test_chunker_persists_two_sentence_preview_for_each_section() -> None:
    result = MarkdownChunker().chunk(
        "# Alpha\n\nFirst sentence. Second sentence. Third sentence.\n"
    )

    assert result.sections[0].preview == "First sentence.Second sentence."
