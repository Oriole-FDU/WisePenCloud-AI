from chat.application.utils.chunkers import (
    BlockKind,
    Chunk,
    ChunkRole,
    TextBlock,
)
from chat.application.utils.chunkers.markdown.locator import build_markdown_locators


def test_incomplete_page_marker_is_ignored() -> None:
    blocks = (
        TextBlock(
            block_id="marker",
            text="<!-- page -->",
            block_kind=BlockKind.PAGE_MARKER,
            block_index=0,
            start_offset=0,
            end_offset=13,
        ),
    )
    chunks = (
        Chunk(
            chunk_id="chunk",
            text="正文",
            chunk_index=0,
            start_offset=13,
            end_offset=15,
        ),
    )

    locators = build_markdown_locators(
        text_length=15,
        blocks=blocks,
        chunks=chunks,
    )

    assert locators == ()


def test_empty_stored_anchor_does_not_create_empty_locator() -> None:
    blocks = (
        TextBlock(
            block_id="table",
            text="ordinary table",
            block_kind=BlockKind.TABLE,
            block_index=0,
            start_offset=0,
            end_offset=14,
            metadata={"anchor_label": ""},
        ),
    )
    chunks = (
        Chunk(
            chunk_id="parent",
            text="ordinary table",
            chunk_index=0,
            role=ChunkRole.PARENT,
            start_offset=0,
            end_offset=14,
            start_block=0,
            end_block=0,
        ),
    )

    locators = build_markdown_locators(
        text_length=14,
        blocks=blocks,
        chunks=chunks,
    )

    assert locators == ()
