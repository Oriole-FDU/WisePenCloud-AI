from chat.application.utils.chunkers import (
    ChunkDocument,
    PlainTextChunker,
    PlainTextChunkerConfig,
)


def test_recursive_plain_text_offsets_handle_overlap() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    result = PlainTextChunker(
        PlainTextChunkerConfig(
            chunk_size=10,
            chunk_overlap=3,
        )
    ).chunk(document=ChunkDocument(text=text))

    assert len(result.chunks) > 1
    for chunk in result.chunks:
        assert chunk.start_offset is not None
        assert chunk.end_offset is not None
        assert text[chunk.start_offset : chunk.end_offset] == chunk.text


def test_plain_text_does_not_build_markdown_locators() -> None:
    result = PlainTextChunker().chunk(
        document=ChunkDocument(text="# 这只是纯文本\n\n正文。")
    )

    assert result.chunker == "plain_text"
    assert result.locators == ()
