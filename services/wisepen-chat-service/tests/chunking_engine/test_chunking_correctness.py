from chat.application.utils.chunking_engine import Chunk, ChunkDocument, ChunkRole
from chat.application.utils.chunking_engine.models import BlockKind
from chat.application.utils.chunking_engine.block_splitters.recursive_text_block_splitter import (
    RecursiveTextBlockSplitter,
    RecursiveTextBlockSplitterConfig,
)
from chat.application.utils.chunking_engine.chunk_normalizers.parent_child_chunk_normalizer import (
    ParentChildChunkNormalizer,
)
from chat.application.utils.chunking_engine.chunk_normalizers.chunk_merge import (
    merge_heading_only,
    merge_short_tails,
)
from chat.application.utils.chunking_engine.registry import get_chunking_engine


def test_markdown_pipeline_offsets_point_to_original_text() -> None:
    text = "# 快速开始\n\n这里是正文。"

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    assert result.chunks
    for chunk in result.chunks:
        assert chunk.start_offset is not None
        assert chunk.end_offset is not None
        assert text[chunk.start_offset:chunk.end_offset].strip() == chunk.text


def test_markdown_block_splitter_keeps_full_section_path() -> None:
    text = "# 一级\n\n## 二级\n\n正文。"

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    paragraph = next(block for block in result.blocks if block.text == "正文。")
    assert paragraph.section_path == ("一级", "二级")
    assert any(
        locator.name == "section:一级 > 二级"
        for locator in result.locators
    )


def test_markdown_block_splitter_marks_pipe_table_blocks() -> None:
    text = "# 指标\n\n| A | B |\n|---|---|\n| 1 | 2 |"

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    table = next(block for block in result.blocks if block.text.startswith("| A | B |"))
    assert table.block_kind == BlockKind.TABLE
    assert table.section_path == ("指标",)
    assert text[table.start_offset:table.end_offset].strip() == table.text
    assert any(
        BlockKind.TABLE in chunk.metadata.get("block_kinds", ())
        for chunk in result.chunks
    )


def test_markdown_block_splitter_marks_html_table_blocks() -> None:
    text = "<table>\n<tr><td>A</td></tr>\n</table>"

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].block_kind == BlockKind.TABLE
    assert text[result.blocks[0].start_offset:result.blocks[0].end_offset].strip() == text


def test_markdown_block_splitter_merges_pdf_table_caption_with_table() -> None:
    text = _captioned_transformer_table_sample()

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    table = next(block for block in result.blocks if "Layer Type" in block.text)
    assert table.block_kind == BlockKind.TABLE
    assert table.text.startswith("·  Table 1: Maximum path lengths")
    assert text[table.start_offset:table.end_offset].strip() == table.text
    assert not any(
        block.block_kind == BlockKind.PARAGRAPH
        and block.text.startswith("·  Table 1: Maximum path lengths")
        for block in result.blocks
    )
    assert any(locator.name == "anchor:Table 1" for locator in result.locators)


def test_parent_child_normalizer_remaps_children_after_heading_only_merge() -> None:
    chunks = (
        Chunk(
            chunk_id="parent-heading",
            text="# 标题",
            chunk_index=0,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="parent-body",
            text="正文内容。",
            chunk_index=1,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="child-body",
            text="正文内容。",
            chunk_index=2,
            role=ChunkRole.CHILD,
            parent_chunk_id="parent-body",
        ),
    )

    normalized = ParentChildChunkNormalizer(min_size=0).process(chunks=chunks)

    parent_ids = {
        chunk.chunk_id
        for chunk in normalized
        if chunk.role == ChunkRole.PARENT
    }
    children = [chunk for chunk in normalized if chunk.role == ChunkRole.CHILD]

    assert children
    assert all(child.parent_chunk_id in parent_ids for child in children)


def test_heading_only_merge_only_treats_markdown_headings_as_headings() -> None:
    chunks = (
        Chunk(
            chunk_id="section-label",
            text="Section: Intro",
            chunk_index=0,
        ),
        Chunk(
            chunk_id="body",
            text="正文内容。",
            chunk_index=1,
        ),
    )

    result = merge_heading_only(chunks)

    assert result.chunks == chunks
    assert result.remapped_ids == {}


def test_short_tail_merge_uses_pair_merge_contract() -> None:
    chunks = (
        Chunk(
            chunk_id="body",
            text="正文内容。",
            chunk_index=0,
            start_offset=0,
            end_offset=5,
            start_block=0,
            end_block=0,
            content_hash="old-hash",
        ),
        Chunk(
            chunk_id="tail",
            text="短尾。",
            chunk_index=1,
            start_offset=7,
            end_offset=10,
            start_block=1,
            end_block=1,
            content_hash="tail-hash",
        ),
    )

    result = merge_short_tails(chunks, min_size=10)

    assert len(result.chunks) == 1
    assert result.chunks[0].chunk_id == "body"
    assert result.chunks[0].text == "正文内容。\n\n短尾。"
    assert result.chunks[0].end_offset == 10
    assert result.chunks[0].end_block == 1
    assert result.chunks[0].content_hash == ""
    assert result.remapped_ids == {"tail": "body"}


def test_short_tail_merge_can_cross_pages_when_page_boundary_is_disabled() -> None:
    chunks = (
        Chunk(
            chunk_id="page-1",
            text="第一页正文。",
            chunk_index=0,
            metadata={"page_label": "1"},
        ),
        Chunk(
            chunk_id="page-2-tail",
            text="第二页短尾。",
            chunk_index=1,
            metadata={"page_label": "2"},
        ),
    )

    blocked = merge_short_tails(chunks, min_size=20)
    allowed = merge_short_tails(
        chunks,
        min_size=20,
        respect_page_boundaries=False,
    )

    assert blocked.chunks == chunks
    assert blocked.remapped_ids == {}
    assert len(allowed.chunks) == 1
    assert allowed.chunks[0].text == "第一页正文。\n\n第二页短尾。"
    assert allowed.remapped_ids == {"page-2-tail": "page-1"}


def test_recursive_splitter_offsets_handle_overlap() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    splitter = RecursiveTextBlockSplitter(
        RecursiveTextBlockSplitterConfig(
            chunk_size=10,
            chunk_overlap=3,
            separators=("",),
        )
    )

    blocks = splitter.split(document=ChunkDocument(text=text))

    assert len(blocks) > 1
    for block in blocks:
        assert block.start_offset is not None
        assert block.end_offset is not None
        assert text[block.start_offset:block.end_offset] == block.text


def _captioned_transformer_table_sample() -> str:
    return (
        "·  Table 1: Maximum path lengths, per-layer complexity and minimum number "
        "of sequential operations for different layer types. _n_ is the sequence "
        "length, _d_ is the representation dimension, _k_ is the kernel size of "
        "convolutions and _r_ the size of the neighborhood in restricted "
        "self-attention. \n\n"
        "|Layer Type|Complexity per Layer|Sequential|Maximum Path Length|\n"
        "|---|---|---|---|\n"
        "|||Operations||\n"
        "|Self-Attention|_O_(_n_2 _· d_)|_O_(1)|_O_(1)|\n"
        "|Recurrent|_O_(_n · d_2)|_O_(_n_)|_O_(_n_)|\n"
        "|Convolutional|_O_(_k · n · d_2)|_O_(1)|_O_(_logk_(_n_))|\n"
        "|Self-Attention (restricted)|_O_(_r · n · d_)|_O_(1)|_O_(_n/r_)|\n\n\n\n"
        "## **3.5 Positional Encoding** \n"
    )
