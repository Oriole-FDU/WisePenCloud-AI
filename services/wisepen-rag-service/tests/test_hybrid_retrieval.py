"""P1-C 两路候选合并和三路动态父块测试。"""

from dataclasses import replace

from common.utils.document import Section, SourceSpan

from rag.application.document.models import (
    DocChunk,
    Document,
    DocumentStructure,
)
from rag.application.retrieval.hybrid_retriever import (
    _build_dynamic_parents,
    _RankedChunk,
    _union_candidates,
)
from rag.domain.repositories.document_vectors import VectorCandidate

from .conftest import chunk_for_document, document


def test_union_keeps_two_route_ranks_without_fusing_scores() -> None:
    candidates = _union_candidates(
        [
            VectorCandidate("a", "resource", "revision", dense_rank=1),
            VectorCandidate("b", "resource", "revision", dense_rank=2),
        ],
        [
            VectorCandidate("b", "resource", "revision", lexical_rank=1),
            VectorCandidate("c", "resource", "revision", lexical_rank=2),
        ],
    )

    assert [candidate.chunk_id for candidate in candidates] == ["a", "b", "c"]
    assert candidates[1].dense_rank == 2
    assert candidates[1].lexical_rank == 1


def test_hybrid_query_inputs_are_plain_arguments() -> None:
    assert SourceSpan(2, 7).length == 5


def test_short_section_returns_its_complete_parent() -> None:
    item = document(
        resource_id="resource",
        version=1,
        section_id="section",
        raw_content="short section",
    )
    chunk = chunk_for_document(item)

    parents = _build_dynamic_parents(
        [_RankedChunk(chunk=chunk, rank=1, score=0.9)],
        documents={(item.resource_id, item.revision.content_revision): item},
        revision_chunks=[chunk],
    )

    assert parents[0].text == item.raw_content
    assert parents[0].section_id == "section"
    assert parents[0].source_spans == [SourceSpan(0, len(item.raw_content))]
    assert parents[0].matched_chunk_ids == [chunk.chunk_id]


def test_high_coverage_section_returns_complete_section() -> None:
    item, chunks = _document_with_chunks(section_length=5_000, chunk_count=5)
    matched = [
        _RankedChunk(chunk=chunks[index], rank=index + 1, score=0.9 - index / 100)
        for index in (0, 2, 4)
    ]

    parents = _build_dynamic_parents(
        matched,
        documents={(item.resource_id, item.revision.content_revision): item},
        revision_chunks=chunks,
    )

    assert len(parents) == 1
    assert parents[0].text == item.raw_content
    assert parents[0].source_spans == [SourceSpan(0, 5_000)]


def test_long_section_returns_complete_expanded_group_without_budget_cutoff() -> None:
    item, chunks = _document_with_chunks(section_length=9_000, chunk_count=9)
    parents = _build_dynamic_parents(
        [_RankedChunk(chunk=chunks[4], rank=1, score=0.9)],
        documents={(item.resource_id, item.revision.content_revision): item},
        revision_chunks=chunks,
    )

    assert len(parents) == 1
    assert parents[0].source_spans == [SourceSpan(3_000, 6_000)]
    assert parents[0].text == item.raw_content[3_000:6_000]


def _document_with_chunks(*, section_length: int, chunk_count: int) -> tuple[Document, list[DocChunk]]:
    raw_content = "x" * section_length
    base = document(
        resource_id="resource",
        version=1,
        section_id="section",
        raw_content=raw_content,
    )
    section = base.structure.sections[0]
    document_with_section = Document(
        resource_id=base.resource_id,
        revision=base.revision,
        raw_content=raw_content,
        structure=DocumentStructure(
            total_length=section_length,
            sections=(
                Section(
                    section_id=section.section_id,
                    title=section.title,
                    level=section.level,
                    parent_section_id=None,
                    ordinal=0,
                    section_path=section.section_path,
                    own_span=SourceSpan(0, section_length),
                    subtree_span=SourceSpan(0, section_length),
                    content_spans=[SourceSpan(0, section_length)],
                    preview="",
                ),
            ),
        ),
    )
    chunk_length = section_length // chunk_count
    chunks = [
        replace(
            chunk_for_document(document_with_section),
            chunk_id=f"chunk-{index}",
            chunk_index=index,
            raw_text=raw_content[index * chunk_length : (index + 1) * chunk_length],
            source_spans=(SourceSpan(index * chunk_length, (index + 1) * chunk_length),),
        )
        for index in range(chunk_count)
    ]
    return document_with_section, chunks
