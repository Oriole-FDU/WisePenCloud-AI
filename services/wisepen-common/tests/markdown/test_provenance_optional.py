from common.utils.markdown import (
    MarkdownChunker,
    MarkdownNode,
    MarkdownNodeKind,
    MarkdownParser,
)


def test_node_without_parser_provenance_has_no_character_position():
    node = MarkdownNode("n", MarkdownNodeKind.PARAGRAPH, "content")
    assert node.source_spans == []
    assert node.start is None
    assert node.end is None


def test_unicode_line_separator_is_not_a_markdown_newline():
    source = "# 标题\r\n\r\n中文🙂\u2028仍在同一行\r\n"
    nodes = MarkdownParser().parse(source)
    assert len(nodes) == 2
    assert nodes[1].text == "中文🙂\u2028仍在同一行\r\n"
    span = nodes[1].source_spans[0]
    assert source[span.start_offset : span.end_offset] == nodes[1].text


def test_caption_is_not_lost_when_large_table_is_split():
    source = (
        "Table 1: caption evidence\n\n| a | b |\n| - | - |\n" + "| row | data |\n" * 900
    )
    result = MarkdownChunker().chunk(source)
    assert sum("caption evidence" in chunk.text for chunk in result.chunks) == 1
    assert all(chunk.content_token_count <= 1600 for chunk in result.chunks)
    table_id = result.nodes[0].node_id
    assert all(table_id in chunk.node_ids for chunk in result.chunks)


def test_inline_facts_and_ordered_list_start_survive():
    nodes = MarkdownParser().parse(
        "3. [link](https://example.com) **strong** ![alt](image.png)\n"
    )
    assert nodes[0].metadata["start"] == 3

    def walk(node):
        yield node
        for child in node.children:
            yield from walk(child)

    token_types = {node.metadata.get("token_type") for node in walk(nodes[0])}
    assert {"link_open", "strong_open", "image"} <= token_types
