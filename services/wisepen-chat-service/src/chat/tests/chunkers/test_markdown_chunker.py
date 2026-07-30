import pytest

from chat.application.utils.chunkers import (
    BlockKind,
    ChunkDocument,
    MarkdownChunkerConfig,
    MarkdownChunkingStrategy,
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
    assert section.chunk_indices == (0, 1, 2, 3)
    next_section = next(
        locator
        for locator in result.locators
        if locator.name == "section:下一个一级"
    )
    assert next_section.chunk_indices == (4,)


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
    assert table.metadata["caption"].strip() == "Table 2: Complexity by layer."
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
    assert result.blocks[0].metadata["caption"].startswith("Table 3.")
    assert result.blocks[0].metadata["anchor_label"] == "Table 3"


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

    assert result.metadata["strategy"] == "by_page"
    assert [chunk.metadata["page_labels"] for chunk in result.chunks] == [
        ("1",),
        ("2",),
    ]
    assert all("<!-- page" not in chunk.text for chunk in result.chunks)


def test_parser_marks_standalone_images_as_figures() -> None:
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
    figure = result.blocks[1]
    mixed_paragraph = result.blocks[2]

    assert page_marker.block_kind == BlockKind.PAGE_MARKER
    assert page_marker.metadata["page_label"] == "7"
    assert (
        text[page_marker.start_offset : page_marker.end_offset].strip()
        == page_marker.text
    )
    assert figure.block_kind == BlockKind.FIGURE
    assert figure.metadata["page_label"] == "7"
    assert figure.metadata["caption"] == "Figure 2: architecture"
    assert mixed_paragraph.block_kind == BlockKind.PARAGRAPH
    assert any(locator.name == "anchor:Figure 2" for locator in result.locators)


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
    assert result.blocks[0].metadata["caption"].strip() == (
        "Figure 4: System architecture."
    )
    assert any(locator.name == "anchor:Figure 4" for locator in result.locators)


def test_marks_html_figure_and_builds_locator_from_figcaption() -> None:
    text = (
        '<figure>\n<img src="figure.png">\n'
        "<figcaption>Figure 5: System architecture.</figcaption>\n</figure>"
    )
    result = MarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert len(result.blocks) == 1
    assert result.blocks[0].block_kind == BlockKind.FIGURE
    assert result.blocks[0].metadata["caption"] == "Figure 5: System architecture."
    assert any(locator.name == "anchor:Figure 5" for locator in result.locators)


def test_by_page_keeps_a_normal_page_whole() -> None:
    text = "<!-- page 1 -->\n\n# 标题\n\n第一段。\n\n第二段。"
    result = MarkdownChunker(
        MarkdownChunkerConfig(
            strategy=MarkdownChunkingStrategy.BY_PAGE,
            max_characters=100,
            new_after_n_chars=1,
        )
    ).chunk(document=ChunkDocument(text=text))

    assert len(result.chunks) == 1
    assert result.chunks[0].metadata["page_labels"] == ("1",)


def test_by_page_rejects_document_without_page_markers() -> None:
    chunker = MarkdownChunker(
        MarkdownChunkerConfig(strategy=MarkdownChunkingStrategy.BY_PAGE)
    )

    with pytest.raises(ValueError, match="requires page markers"):
        chunker.chunk(document=ChunkDocument(text="# 标题\n\n正文。"))


def test_config_rejects_string_strategy() -> None:
    with pytest.raises(TypeError, match="MarkdownChunkingStrategy"):
        MarkdownChunkerConfig(strategy="by_page")  # type: ignore[arg-type]


def test_by_title_can_keep_one_section_across_pages() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "# 标题",
            "第一页正文。",
            "<!-- page 2 -->",
            "第二页正文。",
        )
    )
    result = MarkdownChunker(
        MarkdownChunkerConfig(strategy=MarkdownChunkingStrategy.BY_TITLE)
    ).chunk(document=ChunkDocument(text=text))

    assert len(result.chunks) == 1
    assert result.metadata["strategy"] == "by_title"
    assert result.chunks[0].metadata["page_labels"] == ("1", "2")
    assert len(result.chunks[0].source_spans) == 3
    assert "<!-- page" not in result.chunks[0].text
    assert result.chunks[0].text == "\n\n".join(
        text[span.start_offset : span.end_offset].strip()
        for span in result.chunks[0].source_spans
    )


def test_by_title_starts_a_new_chunk_for_each_section_with_body() -> None:
    text = "# 第一节\n\n第一节正文。\n\n## 第二节\n\n第二节正文。"
    result = MarkdownChunker(
        MarkdownChunkerConfig(strategy=MarkdownChunkingStrategy.BY_TITLE)
    ).chunk(document=ChunkDocument(text=text))

    assert [chunk.metadata["section_paths"] for chunk in result.chunks] == [
        (("第一节",),),
        (("第一节", "第二节"),),
    ]


def test_by_page_splits_only_an_oversized_page() -> None:
    text = "<!-- page 9 -->\n\n" + "超长正文。" * 20
    result = MarkdownChunker(
        MarkdownChunkerConfig(
            strategy=MarkdownChunkingStrategy.BY_PAGE,
            max_characters=30,
        )
    ).chunk(document=ChunkDocument(text=text))

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
