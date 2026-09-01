import pytest
from common.utils.document import Anchor, DocumentChunker, Page, Section, SourceSpan

from rag_v3.application.document.models import (
    ContentRevision,
    Document,
    DocumentStructure,
)
from rag_v3.application.outline import OutlineBuilder
from rag_v3.application.reading import (
    DocumentReader,
    DocumentReadError,
    SectionReadMode,
    _direct_content,
)
from rag_v3.domain.acl import PermissionScope


class Snapshot:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.load_calls = 0
        self.locate_calls = 0

    async def load_documents(self, resource_ids, *, scope):
        self.load_calls += 1
        return {self.document.resource_id: self.document} if self.document.resource_id in resource_ids else {}

    async def locate_sections(self, section_ids, *, scope):
        self.locate_calls += 1
        return {
            section.section_id: type("Location", (), {"document": self.document, "section": section})()
            for section in self.document.structure.sections
            if section.section_id in section_ids
        }


def _document() -> Document:
    raw = "intro\nA body\nB body\nB child body"
    sections = [
        Section("s-a", "A", 1, None, 0, ("A",), SourceSpan(6, 12), SourceSpan(6, 12), (SourceSpan(6, 12),)),
        Section("s-b", "B", 1, None, 1, ("B",), SourceSpan(13, 19), SourceSpan(13, 34), (SourceSpan(13, 19),)),
        Section("s-b1", "B.1", 2, "s-b", 0, ("B", "B.1"), SourceSpan(20, 34), SourceSpan(20, 34), (SourceSpan(20, 34),)),
    ]
    return Document(
        resource_id="r1",
        revision=ContentRevision.create(resource_id="r1", document_version=1, raw_content=raw),
        raw_content=raw,
        structure=DocumentStructure(
            total_length=len(raw),
            sections=sections,
            pages=[Page(0, "A-1", SourceSpan(0, 20)), Page(1, "A-2", SourceSpan(20, 34))],
            anchors=[Anchor("表1", SourceSpan(14, 16))],
        ),
    )


async def test_read_pages_and_sections_preserve_contract() -> None:
    snapshot = Snapshot(_document())
    reader = DocumentReader(snapshots=snapshot)
    scope = PermissionScope(user_id="u")

    pages = await reader.read_pages("r1", ["A-2", "A-1"], scope=scope)
    assert [page.page_label for page in pages] == ["A-2", "A-1"]
    assert pages[0].content == "B child body"

    direct = await reader.read_sections(["s-b"], mode=SectionReadMode.DIRECT, scope=scope)
    assert direct[0].content == "B body"

    recursive = await reader.read_sections(["s-b"], mode=SectionReadMode.RECURSIVE, max_depth=1, scope=scope)
    assert recursive[0].content == "# B\n\nB body\n\n## B.1\n\nB child body"
    assert snapshot.locate_calls == 2


async def test_unknown_page_is_not_replaced_with_full_document() -> None:
    reader = DocumentReader(snapshots=Snapshot(_document()))
    with pytest.raises(DocumentReadError):
        await reader.read_pages("r1", ["missing"], scope=PermissionScope(user_id="u"))


def test_chunker_without_page_marker_returns_no_pages() -> None:
    assert DocumentChunker().chunk("# title\n\nbody").pages == ()


async def test_outline_neighborhood_and_global_outline() -> None:
    snapshot = Snapshot(_document())
    service = OutlineBuilder(snapshots=snapshot)
    scope = PermissionScope(user_id="u")

    neighborhood = await service.neighborhood(["s-b"], sibling_steps=1, scope=scope)
    item = neighborhood[0]
    assert item.char_count == len("B body")
    assert item.page_range == "A-1-A-2"
    assert "- A {#s-a}" in item.outline
    assert "- B [C]" in item.outline
    assert "- B.1 {#s-b1}" in item.outline

    outline = await service.global_outline("r1", max_level=1, scope=scope)
    assert "- A {#s-a}" in outline
    assert "- B {#s-b}" in outline
    assert "B.1" not in outline
    assert "B.1" in await service.global_outline("r1", max_level=0, scope=scope)


def test_section_concatenation_strips_body_and_title_whitespace() -> None:
    section = Section(
        "s",
        "  Title  ",
        1,
        None,
        0,
        ("Title",),
        SourceSpan(0, 1),
        SourceSpan(0, 1),
        (SourceSpan(0, 10), SourceSpan(10, 21)),
    )
    assert _direct_content("  first  \n\n  second  ", section) == "first\n\nsecond"
