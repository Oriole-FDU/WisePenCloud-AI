from common.utils.chunkers import (
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
        assert chunk.source_spans


def test_keeps_full_section_path_and_locator() -> None:
    text = "# 一级\n\n## 二级\n\n正文。"
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    paragraph = next(block for block in result.blocks if block.text.strip() == "正文。")
    assert paragraph.section_path == ("一级", "二级")
    heading = next(block for block in result.blocks if block.text.strip() == "## 二级")
    assert heading.metadata["heading_level"] == 2
    assert any(locator.name == "section:一级 > 二级" for locator in result.locators)


def test_section_locator_includes_nested_sections_until_next_parent_heading() -> None:
    text = (
        "# 一级\n\n一级正文。\n\n"
        "## 二级甲\n\n二级甲正文。\n\n"
        "### 三级\n\n三级正文。\n\n"
        "## 二级乙\n\n二级乙正文。\n\n"
        "# 下一个一级\n\n下一个一级正文。"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    section = next(locator for locator in result.locators if locator.name == "section:一级")
    next_heading_start = text.index("# 下一个一级")

    assert section.end_offset == next_heading_start
    next_section = next(
        locator
        for locator in result.locators
        if locator.name == "section:下一个一级"
    )
    assert next_section.start_offset == next_heading_start


def test_marks_pipe_and_html_tables() -> None:
    pipe_text = "# 指标\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    pipe_result = MarkdownChunker().chunk(document=ChunkDocument(text=pipe_text))
    table = next(
        block for block in pipe_result.blocks if block.text.startswith("| A | B |")
    )

    assert table.block_kind == BlockKind.TABLE
    assert table.section_path == ("指标",)
    assert pipe_text[table.start_offset : table.end_offset] == table.text
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
    assert text[table.start_offset : table.end_offset] == table.text
    assert any(locator.name == "anchor:Table 1" for locator in result.locators)


def test_merges_table_caption_after_table_and_builds_anchor() -> None:
    text = (
        "|Layer Type|Complexity|\n"
        "|---|---|\n"
        "|Self-Attention|O(n²)|\n\n"
        "Table 2: Complexity by layer.\n\n"
        "后续正文。"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))
    table = next(block for block in result.blocks if "Layer Type" in block.text)

    assert table.block_kind == BlockKind.TABLE
    assert table.text.endswith("Table 2: Complexity by layer.\n")
    assert text[table.start_offset : table.end_offset] == table.text
    assert "caption" not in table.metadata
    assert table.metadata["anchor_label"] == "Table 2"
    assert any(locator.name == "anchor:Table 2" for locator in result.locators)


def test_merges_wrapped_numbered_table_caption() -> None:
    text = (
        "Table 3. Results across all evaluated\n"
        "benchmarks and model sizes.\n\n"
        "|Model|Score|\n"
        "|---|---|\n"
        "|Small|0.8|"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.blocks) == 1
    assert result.blocks[0].metadata["anchor_label"] == "Table 3"
    assert "caption" not in result.blocks[0].metadata


def test_does_not_merge_numbered_reference_or_figure_caption_into_table() -> None:
    for caption in (
        "Table 1 is discussed in the following section.",
        "Figure 1: System architecture.",
    ):
        text = (
            f"{caption}\n\n"
            "|Model|Score|\n"
            "|---|---|\n"
            "|Small|0.8|"
        )
        result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

        assert len(result.blocks) == 2
        assert result.blocks[0].block_kind == BlockKind.PARAGRAPH
        assert result.blocks[1].block_kind == BlockKind.TABLE
        assert "caption" not in result.blocks[1].metadata


def test_page_markers_do_not_create_chunk_boundaries() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "第一页短内容。",
            "<!-- page 2 -->",
            "第二页短内容。",
        )
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.chunks) == 1
    assert result.chunks[0].metadata["page_labels"] == ("1", "2")
    assert all("<!-- page" not in chunk.text for chunk in result.chunks)


def test_page_marker_plugin_only_accepts_standalone_valid_markers() -> None:
    text = "before <!-- page 7 --> after\n\n<!-- page -->\n\n正文。"
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert not any(
        block.block_kind == BlockKind.PAGE_MARKER for block in result.blocks
    )
    assert all(block.block_kind == BlockKind.PARAGRAPH for block in result.blocks)

    valid_result = MarkdownChunker().chunk(
        document=ChunkDocument(text="<!-- page 7 -->  \n\n正文。")
    )
    assert valid_result.blocks[0].block_kind == BlockKind.PAGE_MARKER
    assert valid_result.blocks[0].metadata["page_label"] == "7"


def test_parser_marks_standalone_images_as_figures() -> None:
    text = "\n".join(
        (
            "<!-- page 7 -->",
            "![image](https://example.com/figure.png)",
            "",
            "before ![image](https://example.com/mixed.png) after",
        )
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))
    page_marker = result.blocks[0]
    figure = result.blocks[1]
    mixed_paragraph = result.blocks[2]

    assert page_marker.block_kind == BlockKind.PAGE_MARKER
    assert page_marker.metadata["page_label"] == "7"
    assert (
        text[page_marker.start_offset : page_marker.end_offset].strip()
        == page_marker.text.strip()
    )
    assert figure.block_kind == BlockKind.FIGURE
    assert figure.metadata["page_label"] == "7"
    assert "caption" not in figure.metadata
    assert mixed_paragraph.block_kind == BlockKind.PARAGRAPH
    assert not any(locator.name.startswith("anchor:Figure") for locator in result.locators)


def test_flat_docx_markdown_has_no_sections_but_keeps_figures() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "关于招募全国青少年人工智能大赛\n技术类志愿者的通知",
            "一、招募对象及人数",
            "这是一段没有 Word 标题样式的正文。" * 20,
            "<!-- page 2 -->",
            "五、服务保障",
            "1、志愿服务补贴按日发放。",
            "![image1.png](images/image1.png)",
            "报名问卷：[https://example.com](https://example.com)",
            "![image2.jpeg](images/image2.jpeg)",
            "附：志愿者证书样张",
        )
    )

    default_result = MarkdownChunker().chunk(document=ChunkDocument(text=text))
    small_result = MarkdownChunker(max_characters=120).chunk(
        document=ChunkDocument(text=text)
    )

    assert not any(block.block_kind is BlockKind.HEADING for block in default_result.blocks)
    assert not any(locator.name.startswith("section:") for locator in default_result.locators)
    assert all(not block.section_path for block in default_result.blocks)
    assert [
        block.block_kind
        for block in default_result.blocks
        if block.text.startswith("![image")
    ] == [BlockKind.FIGURE, BlockKind.FIGURE]
    assert len(small_result.chunks) > len(default_result.chunks)
    assert any(
        BlockKind.FIGURE in chunk.metadata["block_kinds"]
        for chunk in small_result.chunks
    )
    assert all("<!-- page" not in chunk.text for chunk in small_result.chunks)


def test_figure_url_is_not_mistaken_for_numbered_caption() -> None:
    result = MarkdownChunker().chunk(
        document=ChunkDocument(text="![architecture](figure2.png)")
    )

    assert result.blocks[0].block_kind == BlockKind.FIGURE
    assert "caption" not in result.blocks[0].metadata
    assert not any(locator.name == "anchor:Figure 2" for locator in result.locators)


def test_merges_figure_caption_and_builds_anchor() -> None:
    text = (
        "![architecture](https://example.com/figure.png)\n\n"
        "Figure 4: System architecture."
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.blocks) == 1
    assert result.blocks[0].block_kind == BlockKind.FIGURE
    assert result.blocks[0].metadata["anchor_label"] == "Figure 4"
    assert "caption" not in result.blocks[0].metadata
    assert any(locator.name == "anchor:Figure 4" for locator in result.locators)


def test_merges_figure_caption_before_figure_and_builds_anchor() -> None:
    text = (
        "Figure 6: System architecture.\n\n"
        "![architecture](https://example.com/figure.png)"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.blocks) == 1
    assert result.blocks[0].block_kind == BlockKind.FIGURE
    assert result.blocks[0].metadata["anchor_label"] == "Figure 6"
    assert text[result.blocks[0].start_offset : result.blocks[0].end_offset] == (
        result.blocks[0].text
    )
    assert "caption" not in result.blocks[0].metadata
    assert any(locator.name == "anchor:Figure 6" for locator in result.locators)


def test_merges_multiline_figure_caption_from_mineru_output() -> None:
    text = (
        "![image](https://example.com/figure.png)\n\n"
        "Figure 1. Claude Skills follow a long-context, progressively disclosed format, "
        "which requires a complex sandboxing\n"
        "system and multiple interactions, thereby posing challenges to robust reasoning."
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.blocks) == 1
    assert result.blocks[0].block_kind == BlockKind.FIGURE
    assert result.blocks[0].metadata["anchor_label"] == "Figure 1"
    assert result.blocks[0].text == text
    assert "caption" not in result.blocks[0].metadata
    assert any(locator.name == "anchor:Figure 1" for locator in result.locators)


def test_link_wrapped_images_remain_paragraphs() -> None:
    result = MarkdownChunker().chunk(
        document=ChunkDocument(
            text="[![image](https://example.com/figure.png)](https://example.com/source)"
        )
    )

    assert result.blocks[0].block_kind == BlockKind.PARAGRAPH


def test_parser_moves_formula_anchor_generation_out_of_locator() -> None:
    result = MarkdownChunker().chunk(
        document=ChunkDocument(text="$$x$$ (Eq. 2)\n")
    )

    assert result.blocks[0].block_kind == BlockKind.FORMULA
    assert result.blocks[0].metadata["anchor_label"] == "Equation 2"
    assert any(locator.name == "anchor:Equation 2" for locator in result.locators)


def test_semantic_section_can_cross_pages() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "# 标题",
            "第一页正文。",
            "<!-- page 2 -->",
            "第二页正文。",
        )
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.chunks) == 1
    assert result.chunks[0].metadata["page_labels"] == ("1", "2")
    assert len(result.chunks[0].source_spans) == 3
    assert "<!-- page" not in result.chunks[0].text
    assert result.chunks[0].text == "\n\n".join(
        text[span.start_offset : span.end_offset].strip()
        for span in result.chunks[0].source_spans
    )


def test_each_section_with_body_starts_a_semantic_chunk() -> None:
    text = "# 第一节\n\n第一节正文。\n\n## 第二节\n\n第二节正文。"
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert [chunk.metadata["section_paths"] for chunk in result.chunks] == [
        (("第一节",),),
        (("第一节", "第二节"),),
    ]


def test_oversized_semantic_section_uses_length_as_safety_fallback() -> None:
    text = "<!-- page 9 -->\n\n# 标题\n\n" + "超长正文。" * 20
    result = MarkdownChunker(max_characters=30).chunk(
        document=ChunkDocument(text=text)
    )

    assert len(result.chunks) > 1
    assert all(len(chunk.text) <= 30 for chunk in result.chunks)
    assert all(chunk.metadata["page_labels"] == ("9",) for chunk in result.chunks)
    assert all(
        chunk.text
        == "\n\n".join(
            text[span.start_offset : span.end_offset].strip()
            for span in chunk.source_spans
        )
        for chunk in result.chunks
    )
