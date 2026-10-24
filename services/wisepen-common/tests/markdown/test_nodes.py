from common.utils.markdown import (
    MarkdownChunker,
    MarkdownParser,
    MarkdownNodeKind,
)


def test_parser_preserves_nested_markdown_structure() -> None:
    nodes = MarkdownParser().parse(
        "# Title\n\n3. first\n4. second\n\n> quote\n>\n> - nested\n"
    )

    assert [node.kind for node in nodes] == [
        MarkdownNodeKind.SECTION,
        MarkdownNodeKind.LIST,
        MarkdownNodeKind.QUOTE,
    ]
    assert nodes[1].metadata["ordered"] is True
    assert [child.kind for child in nodes[1].children] == [
        MarkdownNodeKind.LIST_ITEM,
        MarkdownNodeKind.LIST_ITEM,
    ]
    assert nodes[2].children[1].kind is MarkdownNodeKind.LIST


def test_table_wide_row_stays_complete_with_overflow() -> None:
    source = "| a | b |\n| - | - |\n| " + "x" * 300 + " | " + "y" * 300 + " |\n"
    result = MarkdownChunker(40, 80).chunk(source)

    assert len(result.chunks) == 1
    assert result.chunks[0].text == source.rstrip("\n")
    assert result.chunks[0].overflow is True


def test_code_split_preserves_content_under_token_budget() -> None:
    fence = chr(96) * 3
    source = fence + "python\n" + ("    value = 1\n" * 20) + fence + "\n"
    result = MarkdownChunker(40, 80).chunk(source)

    assert len(result.chunks) > 1
    assert all(chunk.content_token_count <= 80 for chunk in result.chunks)
    assert any("value = 1" in chunk.text for chunk in result.chunks)


def test_chunks_use_token_budget_without_overlap() -> None:
    source = "中文内容。 " * 600
    result = MarkdownChunker(40, 80).chunk(source)

    assert len(result.chunks) > 1
    assert all(chunk.content_token_count <= 80 for chunk in result.chunks)
    assert [chunk.chunk_index for chunk in result.chunks] == list(
        range(len(result.chunks))
    )


def test_offsets_preserve_crlf_and_unicode_characters() -> None:
    source = "# 标题\r\n\r\n中文😀正文\r\n"
    for node in MarkdownParser().parse(source):
        span = node.source_spans[0]
        assert source[span.start_offset : span.end_offset] == node.text


def test_page_comment_is_not_a_page_node() -> None:
    nodes = MarkdownParser().parse("before\n<!-- page 2 -->\nafter\n")

    assert all("page_label" not in node.metadata for node in nodes)
