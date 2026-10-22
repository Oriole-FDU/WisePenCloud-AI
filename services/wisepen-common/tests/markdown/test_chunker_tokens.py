import pytest
from common.utils.markdown import (
    MarkdownChunker,
    MarkdownNode,
    MarkdownNodeKind,
)
from common.utils.markdown.chunking.chunker import ChunkingPolicy
from common.utils.markdown.chunking.packer import ChunkPacker


class CharacterCounter:
    name = "fixture-character-tokens"

    def count(self, text: str) -> int:
        return len(text)

    def split(self, text: str, max_tokens: int) -> tuple[str, ...]:
        return tuple(text[i : i + max_tokens] for i in range(0, len(text), max_tokens))


def _chunker(target=12, threshold=24):
    return MarkdownChunker(target, threshold, token_counter=CharacterCounter())


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.children)


@pytest.mark.parametrize(
    "current,next_size,merge",
    [
        (750, 100, True),
        (700, 400, False),
        (600, 300, True),
        (790, 30, False),
        (100, 1300, True),
        (200, 1800, False),
        (700, 200, True),
        (700, 900, False),
        (700, 100, True),
        (800, 1, False),
        (900, 100, False),
        (1600, 1, False),
    ],
)
def test_packer_uses_target_distance(current, next_size, merge):
    # 用不计换行的 fixture 精确表达示例预算，真实 tokenizer 仍计完整输出。
    class ContentCounter(CharacterCounter):
        def count(self, text):
            return len(text.replace("\n", ""))

    packer = ChunkPacker(ChunkingPolicy(800, 1600, ContentCounter()))
    nodes = (
        MarkdownNode("a", MarkdownNodeKind.PARAGRAPH, "a" * current),
        MarkdownNode("b", MarkdownNodeKind.PARAGRAPH, "b" * next_size),
    )
    chunks = packer.pack(nodes)
    assert len(chunks) == (1 if merge else 2)
    assert chunks[-1].overflow is (next_size > 1600)


def test_between_target_and_threshold_is_atomic_and_sections_do_not_mix():
    result = _chunker().chunk("# A\n\n123456789012345\n\n# B\n\nshort\n")
    assert len(result.chunks) == 2
    assert result.chunks[0].text == "123456789012345\n"
    assert [chunk.section_path for chunk in result.chunks] == [["A"], ["B"]]
    assert len({chunk.section_id for chunk in result.chunks}) == 2


def test_document_title_names_preface_root_section():
    result = _chunker().chunk("前言内容\n\n# 正文\n\nbody\n", title="我的文档")
    assert [section.title for section in result.sections] == [
        "我的文档<文档开头>",
        "正文",
    ]
    assert result.sections[0].section_path == ["我的文档<文档开头>"]
    assert result.chunks[0].section_path == ["我的文档<文档开头>"]


def test_string_input_keeps_default_preface_root_name():
    result = _chunker().chunk("前言内容\n\n# 正文\n\nbody\n")
    assert result.sections[0].title == "文档开头"


def test_empty_sections_have_no_chunks_and_global_order_is_stable():
    result = _chunker().chunk("# Empty\n\n## Child\n\nbody\n\n# Last\n\nlast\n")
    assert len(result.sections) == 3
    assert [chunk.section_path for chunk in result.chunks] == [
        ["Empty", "Child"],
        ["Last"],
    ]
    assert [chunk.chunk_index for chunk in result.chunks] == [0, 1]
    assert (
        result.chunks
        == _chunker().chunk("# Empty\n\n## Child\n\nbody\n\n# Last\n\nlast\n").chunks
    )


def test_heading_does_not_compete_with_large_table():
    source = "# Heading\n\n| a | b |\n| - | - |\n| " + "x" * 90 + " | y |\n"
    result = _chunker(80, 160).chunk(source)
    assert len(result.chunks) == 1
    assert result.chunks[0].text.startswith("| a |")
    assert result.chunks[0].section_path == ["Heading"]


def test_original_ids_are_resolvable_and_repeated_across_text_parts():
    result = _chunker().chunk("# A\n\n" + "word " * 40 + "\n")
    originals = {node.node_id for node in _walk(result.nodes)}
    paragraph = result.nodes[1]
    assert len(result.chunks) > 1
    for chunk in result.chunks:
        assert set(chunk.node_ids) <= originals
        assert paragraph.node_id in chunk.node_ids
        assert len(chunk.node_ids) == len(set(chunk.node_ids))
        assert result.nodes[0].node_id not in chunk.node_ids


def test_duplicate_paragraphs_keep_different_identity():
    result = _chunker().chunk("repeated\n\nrepeated\n")
    assert len(result.chunks) == 2
    assert set(result.chunks[0].node_ids).isdisjoint(result.chunks[1].node_ids)


def test_packer_accepts_nodes_without_provenance():
    packer = ChunkPacker(ChunkingPolicy(12, 24, CharacterCounter()))
    nodes = [
        MarkdownNode("one", MarkdownNodeKind.PARAGRAPH, "paragraph one"),
        MarkdownNode("two", MarkdownNodeKind.PARAGRAPH, "paragraph two"),
    ]

    chunks = packer.pack(nodes)

    assert len(chunks) == 2
    assert all(
        chunk.source_spans == [] and chunk.section_path == [] for chunk in chunks
    )


def test_token_budget_counts_joined_text_and_keeps_overflow_isolated():
    class NonAdditiveCounter(CharacterCounter):
        def count(self, text):
            return 1 if "\n" in text else len(text)

    packer = ChunkPacker(ChunkingPolicy(12, 24, NonAdditiveCounter()))
    nodes = [
        MarkdownNode(str(i), MarkdownNodeKind.PARAGRAPH, text)
        for i, text in enumerate(["short", "x" * 30, "last"])
    ]
    chunks = packer.pack(nodes)
    assert [chunk.text for chunk in chunks] == ["short", "x" * 30, "last"]
    assert [chunk.overflow for chunk in chunks] == [False, True, False]

    normal = ChunkPacker(ChunkingPolicy(12, 24, CharacterCounter()))
    combined = normal.pack(
        [
            MarkdownNode("a", MarkdownNodeKind.PARAGRAPH, "a" * 10),
            MarkdownNode("b", MarkdownNodeKind.PARAGRAPH, "b" * 3),
        ]
    )
    assert len(combined) == 1
    assert combined[0].content_token_count == 14


def test_node_above_target_is_sealed_even_with_non_additive_tokens():
    class NonAdditiveCounter(CharacterCounter):
        def count(self, text):
            return 12 if "\n" in text else len(text)

    # 第一块处于软、硬阈值之间；不能因拼接后 BPE 数变小而继续追加。
    packer = ChunkPacker(ChunkingPolicy(12, 24, NonAdditiveCounter()))
    nodes = [
        MarkdownNode("a", MarkdownNodeKind.PARAGRAPH, "a" * 16),
        MarkdownNode("b", MarkdownNodeKind.PARAGRAPH, "b" * 3),
    ]
    chunks = packer.pack(nodes)
    assert [chunk.text for chunk in chunks] == [node.text for node in nodes]
    assert all(not chunk.overflow for chunk in chunks)


def test_tiny_tail_has_no_special_merge_and_can_join_later_content():
    result = _chunker(10, 24).chunk("aaaaaaaaaa bbbbbbbbbb cccccccccc d")
    assert [chunk.text for chunk in result.chunks] == [
        "aaaaaaaaaa",
        "bbbbbbbbbb",
        "cccccccccc",
        "d",
    ]
    packer = ChunkPacker(ChunkingPolicy(800, 1600, CharacterCounter()))
    chunks = packer.pack(
        [
            MarkdownNode(str(i), MarkdownNodeKind.PARAGRAPH, "x" * size)
            for i, size in enumerate([790, 30, 600])
        ]
    )
    assert [chunk.content_token_count for chunk in chunks] == [790, 631]
