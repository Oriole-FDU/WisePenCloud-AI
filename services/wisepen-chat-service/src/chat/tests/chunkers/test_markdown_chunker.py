from chat.application.utils.chunkers import (
    BlockKind,
    ChunkDocument,
    MarkdownChunker,
)


def test_offsets_point_to_original_text() -> None:
    text = "# 快速开始\n\n这里是正文。"
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert result.chunks
    for chunk in result.chunks:
        assert chunk.start_offset is not None
        assert chunk.end_offset is not None
        assert text[chunk.start_offset : chunk.end_offset].strip() == chunk.text


def test_keeps_full_section_path_and_locator() -> None:
    text = "# 一级\n\n## 二级\n\n正文。"
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    paragraph = next(block for block in result.blocks if block.text == "正文。")
    assert paragraph.section_path == ("一级", "二级")
    assert any(locator.name == "section:一级 > 二级" for locator in result.locators)


def test_marks_pipe_and_html_tables() -> None:
    pipe_text = "# 指标\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    pipe_result = MarkdownChunker().chunk(document=ChunkDocument(text=pipe_text))
    table = next(
        block for block in pipe_result.blocks if block.text.startswith("| A | B |")
    )

    assert table.block_kind == BlockKind.TABLE
    assert table.section_path == ("指标",)
    assert pipe_text[table.start_offset : table.end_offset].strip() == table.text
    assert any(
        BlockKind.TABLE in chunk.metadata.get("block_kinds", ())
        for chunk in pipe_result.chunks
    )

    html_text = "<table>\n<tr><td>A</td></tr>\n</table>"
    html_result = MarkdownChunker().chunk(document=ChunkDocument(text=html_text))
    assert len(html_result.blocks) == 1
    assert html_result.blocks[0].block_kind == BlockKind.TABLE


def test_merges_pdf_table_caption_and_builds_anchor() -> None:
    text = (
        "·  Table 1: Maximum path lengths and per-layer complexity.\n\n"
        "|Layer Type|Complexity|\n"
        "|---|---|\n"
        "|Self-Attention|O(n²)|\n\n"
        "## Next"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))
    table = next(block for block in result.blocks if "Layer Type" in block.text)

    assert table.block_kind == BlockKind.TABLE
    assert table.text.startswith("·  Table 1")
    assert text[table.start_offset : table.end_offset].strip() == table.text
    assert any(locator.name == "anchor:Table 1" for locator in result.locators)


def test_page_markers_are_hard_chunk_boundaries() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "第一页短内容。",
            "<!-- page 2 -->",
            "第二页短内容。",
        )
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert [chunk.metadata["page_label"] for chunk in result.chunks] == ["1", "2"]
    assert all("<!-- page" not in chunk.text for chunk in result.chunks)


def test_parser_keeps_image_and_page_marker_semantics() -> None:
    text = "\n".join(
        (
            "<!-- page 7 -->",
            "![Figure 2: architecture](https://example.com/figure.png)",
            "",
            "before ![Figure 3: mixed](https://example.com/mixed.png) after",
        )
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))
    page_marker = result.blocks[0]
    image = result.blocks[1]
    mixed = result.blocks[2]

    assert page_marker.block_kind == BlockKind.PAGE_MARKER
    assert page_marker.metadata["page_label"] == "7"
    assert (
        text[page_marker.start_offset : page_marker.end_offset].strip()
        == page_marker.text
    )
    assert image.block_kind == BlockKind.IMAGE
    assert image.metadata["alt"] == "Figure 2: architecture"
    assert mixed.block_kind == BlockKind.PARAGRAPH
    assert any(locator.name == "anchor:Figure 2" for locator in result.locators)
