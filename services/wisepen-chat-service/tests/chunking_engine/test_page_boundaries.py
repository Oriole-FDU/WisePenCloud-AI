from chat.application.utils.chunking_engine import ChunkDocument, ChunkRole
from chat.application.utils.chunking_engine.registry import get_chunking_engine


def test_markdown_pipeline_keeps_page_boundaries_as_chunk_boundaries() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "第一页短内容。",
            "<!-- page 2 -->",
            "第二页短内容。",
        )
    )

    result = get_chunking_engine("markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    assert len(result.chunks) == 2
    assert result.chunks[0].metadata["page_label"] == "1"
    assert result.chunks[1].metadata["page_label"] == "2"
    assert "<!-- page 1 -->" not in result.chunks[0].text
    assert "<!-- page 2 -->" not in result.chunks[0].text
    assert "<!-- page 1 -->" not in result.chunks[1].text
    assert "<!-- page 2 -->" not in result.chunks[1].text
    assert (
        text[result.chunks[0].start_offset:result.chunks[0].end_offset].strip()
        == result.chunks[0].text
    )
    assert (
        text[result.chunks[1].start_offset:result.chunks[1].end_offset].strip()
        == result.chunks[1].text
    )


def test_parent_child_markdown_parent_chunks_do_not_cross_pages_after_finalizer_merges() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "# 第一页",
            "第一页短内容。",
            "<!-- page 2 -->",
            "# 第二页",
            "第二页短内容。",
        )
    )

    result = get_chunking_engine("parent_child_markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    parents = [chunk for chunk in result.chunks if chunk.role == ChunkRole.PARENT]

    assert len(parents) == 2
    assert parents[0].metadata["page_label"] == "1"
    assert parents[1].metadata["page_label"] == "2"
    assert "<!-- page 1 -->" not in parents[0].text
    assert "<!-- page 2 -->" not in parents[0].text
    assert "<!-- page 1 -->" not in parents[1].text
    assert "<!-- page 2 -->" not in parents[1].text


def test_parent_child_markdown_child_chunks_inherit_parent_page_label() -> None:
    page_one = "第一页内容。" * 120
    page_two = "第二页内容。" * 120
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            page_one,
            "<!-- page 2 -->",
            page_two,
        )
    )

    result = get_chunking_engine("parent_child_markdown").chunk(
        document=ChunkDocument(text=text, content_type="text/markdown"),
    )

    parents_by_id = {
        chunk.chunk_id: chunk
        for chunk in result.chunks
        if chunk.role == ChunkRole.PARENT
    }
    children = [chunk for chunk in result.chunks if chunk.role == ChunkRole.CHILD]

    assert children
    for child in children:
        parent = parents_by_id[child.parent_chunk_id]
        assert child.metadata["page_label"] == parent.metadata["page_label"]
