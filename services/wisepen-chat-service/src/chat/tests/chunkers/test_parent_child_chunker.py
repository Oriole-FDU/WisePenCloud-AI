from chat.application.utils.chunkers import (
    ChunkDocument,
    ChunkRole,
    ParentChildMarkdownChunker,
)


def test_parents_do_not_cross_pages_after_normalization() -> None:
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
    result = ParentChildMarkdownChunker().chunk(document=ChunkDocument(text=text))
    parents = tuple(chunk for chunk in result.chunks if chunk.role == ChunkRole.PARENT)

    assert len(parents) == 2
    assert [parent.metadata["page_label"] for parent in parents] == ["1", "2"]
    assert all("<!-- page" not in parent.text for parent in parents)


def test_children_keep_parent_reference_and_page_label() -> None:
    text = "\n\n".join(
        (
            "<!-- page 1 -->",
            "第一页内容。" * 120,
            "<!-- page 2 -->",
            "第二页内容。" * 120,
        )
    )
    result = ParentChildMarkdownChunker().chunk(document=ChunkDocument(text=text))
    parents = {
        chunk.chunk_id: chunk
        for chunk in result.chunks
        if chunk.role == ChunkRole.PARENT
    }
    children = tuple(chunk for chunk in result.chunks if chunk.role == ChunkRole.CHILD)

    assert children
    assert all(child.parent_chunk_id in parents for child in children)
    assert all(
        child.metadata["page_label"]
        == parents[child.parent_chunk_id].metadata["page_label"]
        for child in children
    )


def test_parent_child_reuses_markdown_locators() -> None:
    text = "# 指标\n\nTable 2: Values\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    result = ParentChildMarkdownChunker().chunk(document=ChunkDocument(text=text))

    assert any(locator.name == "section:指标" for locator in result.locators)
    anchor = next(
        locator for locator in result.locators if locator.name == "anchor:Table 2"
    )
    parent_ids = {
        chunk.chunk_id for chunk in result.chunks if chunk.role == ChunkRole.PARENT
    }
    assert anchor.chunk_ids[0] in parent_ids
