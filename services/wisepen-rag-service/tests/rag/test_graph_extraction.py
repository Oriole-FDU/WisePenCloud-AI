from __future__ import annotations

from dataclasses import replace

import pytest
from neo4j_graphrag.experimental.components.types import Neo4jGraph
from neo4j_graphrag.llm.types import LLMResponse

from rag.application.rag.graph_extraction import (
    KnowledgeExtractionChunk,
    KnowledgeExtractionSource,
    KnowledgeGraphExtractor,
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
    KnowledgeWindowSourceSpan,
    QueryClientGraphRagLLM,
    build_extraction_windows,
)
from rag.application.rag.graph_extraction.models import KnowledgeExtractionWindow
from rag.application.rag.graph_extraction.result_mapper import (
    KnowledgeGraphResultMapper,
)
from rag.application.rag.ingestion import (
    RagDocumentContent,
    RagSectionProjector,
    RagSourceRef,
)
from common.utils.chunkers import SourceSpan


class _QueryClient:
    model = "query-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def query(self, prompt, *, messages=None, response_format=None):
        self.calls.append((prompt, messages, response_format))
        return LLMResponse(content=self.content)

    async def aquery(self, prompt, *, messages=None, response_format=None):
        self.calls.append((prompt, messages, response_format))
        return LLMResponse(content=self.content)


class _MemoryExtractionCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_many(self, keys):
        return {key: self.values[key] for key in keys if key in self.values}

    async def set_many(self, values):
        self.values.update(values)


def _extraction_source(projection, *, content_revision="revision-1"):
    return KnowledgeExtractionSource(
        resource_id=projection.resource_id,
        document_version=projection.document_version,
        content_revision=content_revision,
        markdown=projection.markdown,
        chunks=tuple(
            KnowledgeExtractionChunk(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                section_id=chunk.section_id,
                section_path=chunk.section_path,
                raw_text=chunk.raw_text,
                source_spans=chunk.source_spans,
            )
            for chunk in projection.retrieval_chunks
        ),
        source_refs=projection.source_refs,
    )


def test_extraction_windows_keep_adjacent_context_inside_section() -> None:
    projection = RagSectionProjector().project(
        RagDocumentContent(
            resource_id="resource-1",
            document_version=1,
            markdown="# A\n\nA1",
        )
    )
    base_chunk = projection.retrieval_chunks[0]
    base_source_ref = projection.source_refs[0]
    projection = replace(
        projection,
        markdown="A1\nA2\nB1",
        retrieval_chunks=(
            replace(
                base_chunk,
                chunk_id="chunk-a1",
                chunk_index=0,
                raw_text="A1",
                source_spans=(SourceSpan(0, 2),),
                section_id="section-a",
                section_path=("A",),
            ),
            replace(
                base_chunk,
                chunk_id="chunk-a2",
                chunk_index=1,
                raw_text="A2",
                source_spans=(SourceSpan(3, 5),),
                section_id="section-a",
                section_path=("A",),
            ),
            replace(
                base_chunk,
                chunk_id="chunk-b1",
                chunk_index=2,
                raw_text="B1",
                source_spans=(SourceSpan(6, 8),),
                section_id="section-b",
                section_path=("B",),
            ),
        ),
        source_refs=(
            replace(
                base_source_ref,
                ref_id="ref-a1",
                chunk_id="chunk-a1",
                section_id="section-a",
                section_path=("A",),
                source_spans=(SourceSpan(0, 2),),
            ),
            replace(
                base_source_ref,
                ref_id="ref-a2",
                chunk_id="chunk-a2",
                section_id="section-a",
                section_path=("A",),
                source_spans=(SourceSpan(3, 5),),
            ),
            replace(
                base_source_ref,
                ref_id="ref-b1",
                chunk_id="chunk-b1",
                section_id="section-b",
                section_path=("B",),
                source_spans=(SourceSpan(6, 8),),
            ),
        ),
    )

    windows = build_extraction_windows(_extraction_source(projection))

    assert [
        (window.previous_context, window.current_text, window.next_context)
        for window in windows
    ] == [
        ("", "A1", "A2"),
        ("A1", "A2", ""),
        ("", "B1", ""),
    ]
    assert windows[1].source_mappings == (
        KnowledgeWindowSourceSpan(
            local_start=0,
            local_end=2,
            source_start=3,
            source_end=5,
        ),
    )


def test_result_mapper_uses_explicit_offsets_and_stable_source_ref_selection() -> (
    None
):
    graph = Neo4jGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "chunk-1:alpha",
                    "label": "Entity",
                    "properties": {
                        "name": "Alpha",
                        "entity_type": "product",
                        "evidence_quote": "Alpha",
                    },
                },
                {
                    "id": "chunk-1:beta",
                    "label": "Entity",
                    "properties": {
                        "name": "Beta",
                        "entity_type": "technology",
                        "evidence_quote": "Beta",
                    },
                },
            ],
            "relationships": [
                {
                    "start_node_id": "chunk-1:alpha",
                    "end_node_id": "chunk-1:beta",
                    "type": "DEPENDS_ON",
                    "properties": {
                        "evidence_quote": "Alpha depends on Beta.",
                        "assertion": assertion,
                    },
                }
                for assertion in ("affirmed", "uncertain")
            ],
        }
    )
    original = _window()
    prefix = "Injected heading\n"
    broad_source = replace(
        original.source_refs[0],
        ref_id="source-broad",
        source_spans=(SourceSpan(90, 130),),
    )
    narrow_source = replace(original.source_refs[0], ref_id="source-narrow")
    window = replace(
        original,
        current_text=prefix + original.current_text,
        source_mappings=(
            KnowledgeWindowSourceSpan(
                local_start=len(prefix),
                local_end=len(prefix) + len(original.current_text),
                source_start=100,
                source_end=122,
            ),
        ),
        source_refs=(broad_source, narrow_source),
    )

    result = KnowledgeGraphResultMapper(
        frozenset({KnowledgeRelationType.DEPENDS_ON})
    ).map(graph, window)

    assert len(result.relations) == 1
    assert {
        relation.evidence.source_ref_id for relation in result.relations
    } == {"source-narrow"}
    assert result.relations[0].evidence.quote == "Alpha depends on Beta."


def test_extraction_windows_reject_unmapped_source_text() -> None:
    projection = RagSectionProjector().project(
        RagDocumentContent(
            resource_id="resource-1",
            document_version=1,
            markdown="# A\n\nOriginal text.",
        )
    )
    projection = replace(
        projection,
        retrieval_chunks=(
            replace(projection.retrieval_chunks[0], raw_text="Rewritten text."),
        ),
    )

    with pytest.raises(ValueError, match=projection.retrieval_chunks[0].chunk_id):
        build_extraction_windows(
            _extraction_source(projection),
        )


@pytest.mark.asyncio
async def test_sdk_extractor_keeps_only_schema_valid_source_backed_graph() -> None:
    graph = Neo4jGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "resource",
                    "label": "Resource",
                    "properties": {
                        "name": "resource-1",
                        "resource_id": "resource-1",
                    },
                },
                {
                    "id": "alpha",
                    "label": "Entity",
                    "properties": {
                        "name": "Alpha",
                        "entity_type": "product",
                        "evidence_quote": "Alpha",
                    },
                },
                {
                    "id": "beta",
                    "label": "Entity",
                    "properties": {
                        "name": "Beta",
                        "entity_type": "technology",
                        "evidence_quote": "Beta",
                    },
                },
                {
                    "id": "context-only",
                    "label": "Entity",
                    "properties": {
                        "name": "Earlier",
                        "entity_type": "concept",
                        "evidence_quote": "Earlier",
                    },
                },
            ],
            "relationships": [
                {
                    "start_node_id": "alpha",
                    "end_node_id": "beta",
                    "type": "DEPENDS_ON",
                    "properties": {
                        "evidence_quote": "Alpha depends on Beta.",
                        "assertion": "affirmed",
                    },
                },
                {
                    "start_node_id": "resource",
                    "end_node_id": "alpha",
                    "type": "DEFINES",
                    "properties": {
                        "evidence_quote": "Alpha",
                        "assertion": "affirmed",
                    },
                },
                {
                    "start_node_id": "alpha",
                    "end_node_id": "beta",
                    "type": "RELATED_TO",
                    "properties": {
                        "evidence_quote": "Alpha depends on Beta.",
                        "assertion": "affirmed",
                    },
                },
                {
                    "start_node_id": "alpha",
                    "end_node_id": "context-only",
                    "type": "RELATED_TO",
                    "properties": {
                        "evidence_quote": "Earlier",
                        "assertion": "affirmed",
                        "predicate": "follows",
                    },
                },
            ],
        }
    )
    client = _QueryClient(graph.model_dump_json())
    extractor = KnowledgeGraphExtractor(llm=QueryClientGraphRagLLM(client=client))
    window = _window()

    results = await extractor.extract((window,))

    assert len(results) == 1
    result = results[0]
    assert {node.label for node in result.nodes} == {
        "resource-1",
        "Alpha",
        "Beta",
    }
    assert {node.kind for node in result.nodes} == {
        KnowledgeNodeKind.RESOURCE,
        KnowledgeNodeKind.ENTITY,
    }
    assert [relation.relation_type for relation in result.relations] == [
        KnowledgeRelationType.DEPENDS_ON,
        KnowledgeRelationType.DEFINES,
    ]
    evidence = result.relations[0].evidence
    assert evidence.quote == "Alpha depends on Beta."
    assert evidence.source_ref_id == "source-1"
    prompt, messages, response_format = client.calls[0]
    assert "CURRENT_RESOURCE:" in prompt
    assert "resource_id: resource-1" in prompt
    assert "CURRENT_CHUNK:\nAlpha depends on Beta." in prompt
    assert "then a REQUIRES relation" in prompt
    assert messages == []
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Neo4jGraph"

    core_client = _QueryClient(graph.model_dump_json())
    core_extractor = KnowledgeGraphExtractor(
        llm=QueryClientGraphRagLLM(client=core_client),
        profiles=frozenset({KnowledgeRelationProfile.CORE}),
    )
    core_result = await core_extractor.extract((window,))

    assert [relation.relation_type for relation in core_result[0].relations] == [
        KnowledgeRelationType.DEPENDS_ON
    ]
    assert "then a REQUIRES relation" not in core_client.calls[0][0]


def test_extraction_cache_requires_non_empty_profile() -> None:
    with pytest.raises(ValueError, match="cache_profile is required"):
        KnowledgeGraphExtractor(
            llm=QueryClientGraphRagLLM(client=_QueryClient("{}")),
            cache=_MemoryExtractionCache(),
            cache_profile="  ",
        )


@pytest.mark.asyncio
async def test_extraction_cache_relocates_evidence_for_new_revision() -> None:
    graph = Neo4jGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "alpha",
                    "label": "Entity",
                    "properties": {
                        "name": "Alpha",
                        "entity_type": "product",
                        "evidence_quote": "Alpha",
                    },
                },
                {
                    "id": "beta",
                    "label": "Entity",
                    "properties": {
                        "name": "Beta",
                        "entity_type": "technology",
                        "evidence_quote": "Beta",
                    },
                },
            ],
            "relationships": [
                {
                    "start_node_id": "alpha",
                    "end_node_id": "beta",
                    "type": "DEPENDS_ON",
                    "properties": {
                        "evidence_quote": "Alpha depends on Beta.",
                        "assertion": "affirmed",
                    },
                }
            ],
        }
    )
    client = _QueryClient(graph.model_dump_json())
    cache = _MemoryExtractionCache()
    extractor = KnowledgeGraphExtractor(
        llm=QueryClientGraphRagLLM(client=client),
        cache=cache,
        cache_profile="query-model",
    )
    first = _window()

    first_result = await extractor.extract((first,))
    moved_source = replace(
        first.source_refs[0],
        ref_id="source-2",
        document_version=4,
        chunk_id="chunk-2",
        source_spans=(SourceSpan(start_offset=200, end_offset=222),),
    )
    moved = replace(
        first,
        document_version=4,
        content_revision="revision-4",
        chunk_id="chunk-2",
        source_mappings=(
            KnowledgeWindowSourceSpan(
                local_start=0,
                local_end=22,
                source_start=200,
                source_end=222,
            ),
        ),
        source_refs=(moved_source,),
    )
    moved_result = await extractor.extract((moved,))

    assert len(client.calls) == 1
    assert (
        first_result[0].relations[0].evidence.evidence_ref_id
        != moved_result[0].relations[0].evidence.evidence_ref_id
    )
    assert moved_result[0].relations[0].evidence.source_ref_id == "source-2"


def _window() -> KnowledgeExtractionWindow:
    source_ref = RagSourceRef(
        ref_id="source-1",
        resource_id="resource-1",
        document_version=3,
        chunk_id="chunk-1",
        section_id="section-1",
        section_path=("架构", "依赖"),
        source_spans=(SourceSpan(start_offset=100, end_offset=122),),
        page_label="4",
    )
    return KnowledgeExtractionWindow(
        resource_id="resource-1",
        document_version=3,
        content_revision="revision-3",
        chunk_id="chunk-1",
        chunk_index=0,
        current_text="Alpha depends on Beta.",
        source_mappings=(
            KnowledgeWindowSourceSpan(
                local_start=0,
                local_end=22,
                source_start=100,
                source_end=122,
            ),
        ),
        source_refs=(source_ref,),
        section_paths=(("架构", "依赖"),),
        previous_context="Earlier context.",
        next_context="Later context.",
    )
