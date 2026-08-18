from hashlib import sha256
from types import SimpleNamespace

import pytest

from rag.core.persistence.mongo import (
    MongoPublishedResourceReader,
    published_resource_reader,
)
from rag.core.persistence.mongo._source_parts import SourcePart, assemble_source_text
from rag.domain.models.graph import GraphEvidence
from rag.domain.repositories.mongo import (
    PublishedResourceCorruptError,
    PublishedResourceRevisionError,
)
from common.utils.document import SourceSpan


class _Query:
    def __init__(self, records) -> None:
        self._records = records

    def sort(self, *args):
        fields = args[0]
        if isinstance(fields, str):
            attribute = fields.lstrip("+-")
            self._records.sort(key=lambda record: getattr(record, attribute))
        else:
            self._records.sort(
                key=lambda record: tuple(
                    getattr(record, attribute) for attribute, _ in fields
                )
            )
        return self

    async def to_list(self):
        return list(self._records)


def _find_entity(records):
    class _Entity:
        @classmethod
        def find(cls, query):
            return _Query(records)

    return _Entity


def _install_published_resource(
    monkeypatch,
    *,
    content_hash: str | None = None,
    include_block: bool = True,
) -> None:
    markdown = "TitleBodyTail"
    revision = SimpleNamespace(
        resource_id="resource-1",
        content_revision="revision-1",
        document_version=1,
        content_hash=content_hash or sha256(markdown.encode("utf-8")).hexdigest(),
        index_schema_version="v1",
        structure_mode="sectioned",
        total_length=len(markdown),
        outline=[],
        pages=[
            SimpleNamespace(
                page_index=0,
                page_label="1",
                start_offset=0,
                end_offset=len(markdown),
            )
        ],
        anchors=[],
    )
    section = SimpleNamespace(
        section_id="section-1",
        title="Title",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["Title"],
        own_start=0,
        own_end=len(markdown),
        subtree_end=len(markdown),
        content_spans=[SimpleNamespace(start_offset=5, end_offset=9)],
        preview="Body",
    )
    following_section = SimpleNamespace(
        section_id="section-2",
        title="Tail",
        level=1,
        parent_section_id=None,
        ordinal=1,
        section_path=["Tail"],
        own_start=9,
        own_end=13,
        subtree_end=13,
        content_spans=[SimpleNamespace(start_offset=9, end_offset=13)],
        preview="Tail",
    )
    block = SimpleNamespace(
        block_id="block-1",
        section_id="section-1",
        ordinal=0,
        raw_text="Body",
        start_offset=5,
        source_spans=[SimpleNamespace(start_offset=5, end_offset=9)],
        page_labels=["1"],
        anchor_labels=[],
    )
    following_block = SimpleNamespace(
        block_id="block-2",
        section_id="section-2",
        ordinal=0,
        raw_text="Tail",
        start_offset=9,
        source_spans=[SimpleNamespace(start_offset=9, end_offset=13)],
        page_labels=["1"],
        anchor_labels=[],
    )
    source_ref = SimpleNamespace(
        ref_id="ref-1",
        resource_id="resource-1",
        content_revision="revision-1",
        chunk_id="chunk-1",
        reading_block_id="block-1",
        section_id="section-1",
        section_path=["Title"],
        source_spans=[SimpleNamespace(start_offset=5, end_offset=9)],
        page_labels=["1"],
        anchor_labels=[],
    )
    source_part = SimpleNamespace(
        resource_id="resource-1",
        content_revision="revision-1",
        part_index=0,
        start_offset=0,
        end_offset=len(markdown),
        text=markdown,
    )

    class _StateEntity:
        @classmethod
        async def find_one(cls, query):
            return SimpleNamespace(applied_content_revision="revision-1")

    class _RevisionEntity:
        @classmethod
        async def find_one(cls, query):
            return revision

    monkeypatch.setattr(
        published_resource_reader,
        "ResourceIndexStateEntity",
        _StateEntity,
    )
    monkeypatch.setattr(
        published_resource_reader,
        "ContentRevisionEntity",
        _RevisionEntity,
    )
    monkeypatch.setattr(
        published_resource_reader,
        "SectionEntity",
        _find_entity([following_section, section]),
    )
    monkeypatch.setattr(
        published_resource_reader,
        "ReadingBlockEntity",
        _find_entity([following_block, block] if include_block else []),
    )
    monkeypatch.setattr(
        published_resource_reader,
        "SourceRefEntity",
        _find_entity([source_ref]),
    )
    monkeypatch.setattr(
        published_resource_reader,
        "SourcePartEntity",
        _find_entity([source_part]),
    )


@pytest.mark.asyncio
async def test_reader_returns_none_without_published_revision(monkeypatch) -> None:
    class _MissingState:
        @classmethod
        async def find_one(cls, query):
            return None

    monkeypatch.setattr(
        published_resource_reader,
        "ResourceIndexStateEntity",
        _MissingState,
    )
    reader = MongoPublishedResourceReader()

    assert await reader.get_content_revision("resource-1") is None
    assert await reader.get_document_outline("resource-1") is None
    assert await reader.get_pages("resource-1", ["1"]) is None
    assert await reader.get_sections("resource-1", ["section-1"]) is None
    assert (
        await reader.get_source_evidence("resource-1", "revision-1", ["ref-1"]) is None
    )
    assert (
        await reader.get_graph_evidence(
            "resource-1",
            "revision-1",
            [_graph_evidence()],
        )
        is None
    )
    with pytest.raises(PublishedResourceRevisionError):
        await reader.get_graph_build_source("resource-1", "revision-1")


@pytest.mark.asyncio
async def test_reader_projects_structure_content_evidence_and_graph_source(
    monkeypatch,
) -> None:
    _install_published_resource(monkeypatch)
    reader = MongoPublishedResourceReader()

    outline = await reader.get_document_outline("resource-1")
    pages = await reader.get_pages("resource-1", ["1"])
    sections = await reader.get_sections("resource-1", ["section-1"])
    evidence = await reader.get_source_evidence(
        "resource-1",
        "revision-1",
        ["ref-1"],
    )
    graph_source = await reader.get_graph_build_source(
        "resource-1",
        "revision-1",
    )
    graph_evidence = await reader.get_graph_evidence(
        "resource-1",
        "revision-1",
        [_graph_evidence()],
    )

    assert outline is not None
    assert outline.content_revision == "revision-1"
    assert outline.outline == []
    assert pages is not None and pages["1"] == "TitleBodyTail"
    assert sections is not None and sections["section-1"].text == "Body"
    assert sections["section-1"].next is not None
    assert sections["section-1"].next.section_id == "section-2"
    assert evidence is not None and evidence["ref-1"].source_text == "Body"
    assert graph_source.markdown == "TitleBodyTail"
    assert [section.section_id for section in graph_source.structure.sections] == [
        "section-1",
        "section-2",
    ]
    assert [block.block_id for block in graph_source.reading_blocks] == [
        "block-1",
        "block-2",
    ]
    assert graph_evidence is not None
    assert graph_evidence["evidence-1"].block_range == SourceSpan(0, 4)
    assert graph_evidence["evidence-1"].reading_block.raw_text == "Body"


@pytest.mark.asyncio
async def test_reader_rejects_stale_revision_and_corrupt_evidence(monkeypatch) -> None:
    _install_published_resource(monkeypatch)
    reader = MongoPublishedResourceReader()

    with pytest.raises(PublishedResourceRevisionError):
        await reader.get_source_evidence("resource-1", "stale", ["ref-1"])

    _install_published_resource(monkeypatch, content_hash="bad-hash")
    with pytest.raises(PublishedResourceCorruptError, match="hash"):
        await reader.get_source_evidence("resource-1", "revision-1", ["ref-1"])


@pytest.mark.asyncio
async def test_reader_rejects_source_ref_without_ownership_records(monkeypatch) -> None:
    _install_published_resource(monkeypatch, include_block=False)

    with pytest.raises(PublishedResourceCorruptError, match="ownership"):
        await MongoPublishedResourceReader().get_source_evidence(
            "resource-1",
            "revision-1",
            ["ref-1"],
        )


@pytest.mark.asyncio
async def test_reader_rejects_graph_evidence_outside_its_reading_block(
    monkeypatch,
) -> None:
    _install_published_resource(monkeypatch)
    evidence = _graph_evidence()
    evidence.source_span = SourceSpan(9, 13)
    evidence.quote = "Tail"

    with pytest.raises(PublishedResourceCorruptError, match="outside"):
        await MongoPublishedResourceReader().get_graph_evidence(
            "resource-1",
            "revision-1",
            [evidence],
        )


def test_source_text_assembly_joins_contiguous_parts() -> None:
    parts = [_part(0, 0, 3, "abc"), _part(1, 3, 6, "def")]

    assert assemble_source_text(parts, [SourceSpan(1, 5)]) == "bcde"


def test_source_text_assembly_rejects_gaps() -> None:
    parts = [_part(0, 0, 3, "abc"), _part(1, 4, 6, "ef")]

    with pytest.raises(RuntimeError, match="gap"):
        assemble_source_text(parts, [SourceSpan(0, 6)])


def test_source_text_assembly_rejects_overlapping_parts() -> None:
    parts = [_part(0, 0, 4, "abcd"), _part(1, 3, 6, "def")]

    with pytest.raises(RuntimeError, match="overlap"):
        assemble_source_text(parts, [SourceSpan(0, 6)])


def _part(index: int, start: int, end: int, text: str) -> SourcePart:
    return SourcePart(
        resource_id="resource-1",
        content_revision="revision-1",
        part_index=index,
        source_span=SourceSpan(start, end),
        text=text,
    )


def _graph_evidence() -> GraphEvidence:
    return GraphEvidence(
        evidence_id="evidence-1",
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id="block-1",
        source_span=SourceSpan(5, 9),
        quote="Body",
    )
