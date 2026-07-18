from chat.application.utils.chunkers import Chunk, ChunkRole
from chat.application.utils.chunkers._utils.normalization import (
    merge_heading_only,
    merge_short_tails,
    normalize_flat_chunks,
    normalize_parent_child_chunks,
)


def test_heading_merge_only_treats_markdown_headings_as_headings() -> None:
    chunks = (
        Chunk(chunk_id="section-label", text="Section: Intro", chunk_index=0),
        Chunk(chunk_id="body", text="正文内容。", chunk_index=1),
    )

    result = merge_heading_only(chunks)

    assert result.chunks == chunks
    assert result.remapped_ids == {}


def test_short_tail_merge_preserves_contract() -> None:
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


def test_short_tail_never_merges_across_pages() -> None:
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

    result = merge_short_tails(chunks, min_size=20)

    assert result.chunks == chunks
    assert result.remapped_ids == {}


def test_consecutive_headings_never_merge_across_pages() -> None:
    chunks = (
        Chunk(
            chunk_id="page-1-heading",
            text="# 第一页",
            chunk_index=0,
            metadata={"page_label": "1"},
        ),
        Chunk(
            chunk_id="page-2-heading",
            text="# 第二页",
            chunk_index=1,
            metadata={"page_label": "2"},
        ),
    )

    result = merge_heading_only(chunks)

    assert result.chunks == chunks
    assert result.remapped_ids == {}


def test_parent_normalization_remaps_children_after_heading_merge() -> None:
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

    normalized = normalize_parent_child_chunks(chunks)
    parent_ids = {
        chunk.chunk_id for chunk in normalized if chunk.role == ChunkRole.PARENT
    }
    children = tuple(chunk for chunk in normalized if chunk.role == ChunkRole.CHILD)

    assert children
    assert all(child.parent_chunk_id in parent_ids for child in children)


def test_parent_normalization_remaps_consecutive_heading_children() -> None:
    chunks = (
        Chunk(
            chunk_id="first-heading",
            text="# 第一节",
            chunk_index=0,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="second-heading",
            text="## 第二节",
            chunk_index=1,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="second-heading-child",
            text="## 第二节",
            chunk_index=2,
            role=ChunkRole.CHILD,
            parent_chunk_id="second-heading",
        ),
    )

    normalized = normalize_parent_child_chunks(chunks)
    parent_ids = {
        chunk.chunk_id for chunk in normalized if chunk.role == ChunkRole.PARENT
    }
    child = next(chunk for chunk in normalized if chunk.role == ChunkRole.CHILD)

    assert child.parent_chunk_id in parent_ids


def test_heading_group_merged_backward_maps_every_child_to_survivor() -> None:
    chunks = (
        Chunk(
            chunk_id="body",
            text="正文内容足够长。" * 50,
            chunk_index=0,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="first-heading",
            text="# 第一节",
            chunk_index=1,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="second-heading",
            text="## 第二节",
            chunk_index=2,
            role=ChunkRole.PARENT,
        ),
        Chunk(
            chunk_id="second-heading-child",
            text="## 第二节",
            chunk_index=3,
            role=ChunkRole.CHILD,
            parent_chunk_id="second-heading",
        ),
    )

    normalized = normalize_parent_child_chunks(chunks)
    parent_ids = {
        chunk.chunk_id for chunk in normalized if chunk.role == ChunkRole.PARENT
    }
    child = next(chunk for chunk in normalized if chunk.role == ChunkRole.CHILD)

    assert len(parent_ids) == 1
    assert child.parent_chunk_id in parent_ids


def test_assign_chunk_ids_recomputes_stale_hash() -> None:
    chunk = Chunk(
        chunk_id="chunk",
        text="最终文本",
        chunk_index=0,
        content_hash="stale-hash",
    )

    finalized = normalize_flat_chunks((chunk,))

    assert finalized[0].content_hash != "stale-hash"
