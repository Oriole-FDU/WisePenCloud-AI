from common.utils.chunkers import (
    BlockKind,
    TextBlock,
)
from common.utils.chunkers.markdown.locator import build_markdown_locators


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
    locators = build_markdown_locators(
        text_length=15,
        blocks=blocks,
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
    locators = build_markdown_locators(
        text_length=14,
        blocks=blocks,
    )

    assert locators == ()
