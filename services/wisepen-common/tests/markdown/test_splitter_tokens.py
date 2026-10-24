import pytest
from common.utils.markdown import (
    MarkdownChunker,
    MarkdownNode,
    MarkdownNodeKind,
    MarkdownParser,
    TiktokenTokenCounter,
)
from common.utils.markdown.chunking.chunker import ChunkingPolicy
from common.utils.markdown.chunking.packer import ChunkPacker
from common.utils.markdown.chunking.splitter import StructuralNodeSplitter

from .test_chunker_tokens import CharacterCounter


def _policy():
    return ChunkingPolicy(32, 64, TiktokenTokenCounter())


@pytest.mark.parametrize(
    "fence,newline,final_newline", [("~~~", "\n", True), (chr(96) * 4, "\r\n", False)]
)
def test_code_preserves_indentation_and_rebuilds_fences(fence, newline, final_newline):
    opening = fence + "python extra" + newline
    closing = fence + (newline if final_newline else "")
    body = ("    value = '中文🙂'\u2028 # comment" + newline + newline) * 60
    source = opening + body + closing
    node = MarkdownParser().parse(source)[0]
    policy = _policy()
    parts = StructuralNodeSplitter(policy).split(node)
    assert len(parts) > 1
    assert all(
        part.text.startswith(opening) and part.text.endswith(closing) for part in parts
    )
    assert "".join(part.text[len(opening) : -len(closing)] for part in parts) == body
    assert all(part.metadata["language"] == "python" for part in parts)
    assert all(part.metadata["info"] == "python extra" for part in parts)
    assert all(policy.token_counter.count(part.text) <= 64 for part in parts)
    assert all(
        part.source_spans == [] and part.source_node_ids == [node.node_id]
        for part in parts
    )


def test_unclosed_code_gets_synthetic_closing_only_when_split():
    fence = chr(96) * 3
    opening = fence + "python\n"
    body = "    x = 1\n" * 30 + "    tail = 2"
    node = MarkdownParser().parse(opening + body)[0]
    parts = StructuralNodeSplitter(_policy()).split(node)
    assert len(parts) > 1
    assert all(
        part.text.startswith(opening) and part.text.endswith(fence + "\n")
        for part in parts
    )
    assert "".join(part.text[len(opening) : -4] for part in parts) == body + "\n"

    small = MarkdownParser().parse(opening + "    x = 1")[0]
    assert StructuralNodeSplitter(_policy()).split(small) == [small]


def test_code_long_line_is_preserved_and_becomes_overflow():
    fence = chr(96) * 3
    line = "    x = '" + "🙂" * 100 + "'\n"
    source = fence + "python\nshort = 1\n" + line + "last = 2\n" + fence + "\n"
    result = MarkdownChunker(32, 64).chunk(source)
    chunk = next(chunk for chunk in result.chunks if line in chunk.text)
    assert chunk.text == fence + "python\n" + line + fence + "\n"
    assert chunk.overflow
    assert all(
        other.chunk_id != chunk.chunk_id
        for other in result.chunks
        if "short" in other.text or "last" in other.text
    )


def test_indented_code_preserves_exact_lines():
    source = "    x = 1\n    \n    y = 2\n" * 60
    node = MarkdownParser().parse(source)[0]
    parts = StructuralNodeSplitter(_policy()).split(node)
    assert "".join(part.text for part in parts) == node.text
    assert all(part.metadata["fenced"] is False for part in parts)


@pytest.mark.parametrize("kind", [MarkdownNodeKind.FORMULA, MarkdownNodeKind.FIGURE])
def test_formula_and_figure_are_atomic_and_overflow_is_final(kind):
    node = MarkdownNode("atomic", kind, "x=y\n" * 50)
    policy = ChunkingPolicy(10, 24, CharacterCounter())
    assert StructuralNodeSplitter(policy).split(node) == [node]
    chunks = ChunkPacker(policy).pack([node])
    assert chunks[0].text == node.text
    assert chunks[0].overflow is True


def test_table_repeats_header_without_splitting_rows():
    source = "| a | b |\n| - | - |\n" + "".join(
        f"| row{index} | value{index} |\n" for index in range(60)
    )
    node = MarkdownParser().parse(source)[0]
    policy = _policy()
    parts = StructuralNodeSplitter(policy).split(node)
    assert len(parts) > 1
    assert all(part.text.startswith("| a | b |\n| - | - |") for part in parts)
    assert all(policy.token_counter.count(part.text) <= 64 for part in parts)
    assert sum("row0 " in part.text for part in parts) == 1
    original_rows = [
        row for row in node.children if row.kind is MarkdownNodeKind.TABLE_ROW
    ]
    split_rows = [
        row
        for part in parts
        for row in part.children
        if row.kind is MarkdownNodeKind.TABLE_ROW
    ]
    assert split_rows == original_rows
    assert all(node.node_id in part.source_node_ids for part in parts)


@pytest.mark.parametrize("before", [True, False])
def test_table_caption_stays_whole_with_first_row_and_keeps_position(before):
    table = "| a | b |\n| - | - |\n" + "| row | data |\n" * 20
    caption = "Table 1: " + "caption " * 100 + "\n"
    source = caption + "\n" + table if before else table + "\n" + caption
    result = MarkdownChunker(32, 64).chunk(source)
    first = result.chunks[0]
    assert first.overflow
    assert caption.rstrip("\n") in first.text and "| row | data |" in first.text
    if before:
        assert first.text.startswith("Table 1:")
    else:
        assert first.text.endswith(caption.rstrip("\n"))
    assert sum("Table 1:" in chunk.text for chunk in result.chunks) == 1
    assert all(
        chunk.overflow == (chunk.content_token_count > 64) for chunk in result.chunks
    )


def test_wide_row_is_not_reconstructed_as_cell_fragments():
    row = "| " + "word " * 100 + " | " + "value " * 100 + " |\n"
    source = "| a | b |\n| - | - |\n| short | one |\n" + row + "| last | two |\n"
    result = MarkdownChunker(32, 64).chunk(source)
    wide = next(chunk for chunk in result.chunks if row.rstrip("\n") in chunk.text)
    assert wide.overflow
    assert "short" not in wide.text and "last" not in wide.text


def test_html_table_without_row_structure_is_atomic():
    source = "<table><tr><td>" + "value " * 100 + "</td></tr></table>\n"
    node = MarkdownParser().parse(source)[0]
    assert node.kind is MarkdownNodeKind.TABLE and not node.children
    assert StructuralNodeSplitter(_policy()).split(node) == [node]


def test_nested_ordered_list_preserves_oversized_item():
    source = "3. first\n   - nested " + "word " * 100 + "\n4. second\n"
    node = MarkdownParser().parse(source)[0]
    parts = StructuralNodeSplitter(_policy()).split(node)
    items = [child for part in parts for child in part.children]
    assert items == list(node.children)
    assert all(
        part.metadata["ordered"] and part.metadata["start"] == 3 for part in parts
    )
    result = MarkdownChunker(32, 64).chunk(source)
    assert result.chunks[0].overflow
    assert "second" not in result.chunks[0].text


def test_structure_grouping_uses_the_same_distance_rule():
    class ContentCounter(CharacterCounter):
        def count(self, text):
            return len(text.replace("\n", ""))

    # 原列表超硬阈值才拆；拆后按软目标距离分组，完整 item 允许跨过软目标。
    for lengths, expected in [
        ([700, 200, 800], [901, 800]),
        ([700, 900, 200], [700, 900, 200]),
        ([100, 1300, 300], [1401, 300]),
        ([300, 500, 900], [801, 900]),
    ]:
        items = tuple(
            MarkdownNode(str(i), MarkdownNodeKind.LIST_ITEM, "x" * n)
            for i, n in enumerate(lengths)
        )
        node = MarkdownNode(
            "list",
            MarkdownNodeKind.LIST,
            "\n".join(item.text for item in items),
            children=items,
        )
        parts = StructuralNodeSplitter(
            ChunkingPolicy(800, 1600, ContentCounter())
        ).split(node)
        assert [len(part.text) for part in parts] == expected


@pytest.mark.parametrize(
    "source",
    [
        "word " * 15 + "\n",
        "> " + "word " * 15 + "\n",
        "- first item\n- second item\n- third item\n",
        "| a | b |\n| - | - |\n| first | one |\n| second | two |\n",
        "```python\nfirst = 1\nsecond = 2\nthird = 3\n```\n",
    ],
)
@pytest.mark.parametrize("hard_margin", [0, 1])
def test_nodes_between_soft_and_hard_limits_remain_intact(source, hard_margin):
    node = MarkdownParser().parse(source)[0]
    counter = CharacterCounter()
    size = counter.count(node.text)
    policy = ChunkingPolicy(size // 2, size + hard_margin, counter)
    parts = StructuralNodeSplitter(policy).split(node)
    # 等于硬阈值也保留原节点及其 provenance，不生成派生片段。
    assert parts == [node]


def test_text_above_hard_limit_splits_to_soft_target():
    node = MarkdownParser().parse("word " * 30 + "\n")[0]
    policy = ChunkingPolicy(20, 40, CharacterCounter())
    parts = StructuralNodeSplitter(policy).split(node)
    assert len(parts) > 1
    assert all(policy.token_counter.count(part.text) <= 20 for part in parts)
    split_words = [word for part in parts for word in part.text.split()]
    assert split_words == node.text.split()
