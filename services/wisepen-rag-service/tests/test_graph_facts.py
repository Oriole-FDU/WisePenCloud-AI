"""P2-A 图谱事实、插件和文本证据边界测试。"""

from collections import defaultdict
from dataclasses import replace

import pytest
from common.utils.document import DocumentChunkerConfig, SourceSpan
from pydantic import ConfigDict

from rag.application.document.models import (
    DocChunk,
    ResourceIndexState,
)
from rag.application.document.preparation import DocumentPreparer
from rag.application.graph import graph_fact_builder
from rag.application.graph.graph_fact_builder import (
    GraphFactBuilder,
    _collect_llm_facts,
    _ExtractedNode,
    _GraphExtraction,
)
from rag.application.graph.models import (
    GraphEdge,
    GraphNode,
    graph_edge_id,
    graph_node_id,
)
from rag.application.plugins.core import (
    EntitySpec,
    RagPlugin,
    Ontology,
    RelationSpec,
)
from rag.application.plugins.core.metadata import (
    DocChunkMetadata,
    DocumentMetadata,
    GeneralDocumentMetadata,
)
from rag.application.plugins.core.registry import (
    DocumentChunkMetadataBuilder,
    RagPluginRegistry,
)
from rag.application.publication import DocumentPublication
from rag.domain.repositories.graph_topology import GraphSourceProjection

from .conftest import (
    MemoryDocChunks,
    MemoryDocuments,
    MemoryDocumentVectors,
    MemoryIndexStates,
    chunk_for_document,
    document,
)


class PaperMetadata(DocumentMetadata):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_type: str = "paper"
    title: str
    reference_title: str


class PaperChunkMetadata(DocChunkMetadata):
    """论文 Chunk 的段落角色，供图谱抽取策略和后续垂域能力共同消费。"""

    chunk_type: str = "paper"
    section_role: str = ""
    is_graph_extraction_target: bool = False


class _PaperChunkMetadataBuilder:
    doc_metadata_type = PaperMetadata
    chunk_metadata_type = PaperChunkMetadata

    def build_metadata(
        self,
        *,
        document,
        chunk: DocChunk,
    ) -> PaperChunkMetadata:
        section_role = chunk.section_path[-1].casefold() if chunk.section_path else ""
        return PaperChunkMetadata(
            section_role=section_role,
            is_graph_extraction_target=section_role in {"abstract", "摘要"},
        )


class _CitationProducer:
    def produce(self, item):
        metadata = item.metadata
        source = GraphNode(
            node_id=graph_node_id(category="paper", name=metadata.title),
            name=metadata.title,
            category="paper",
        )
        target = GraphNode(
            node_id=graph_node_id(category="paper", name=metadata.reference_title),
            name=metadata.reference_title,
            category="paper",
        )
        return (
            (source, target),
            (
                GraphEdge(
                    edge_id=graph_edge_id(
                        source_node_id=source.node_id,
                        relation_type="CITES",
                        target_node_id=target.node_id,
                    ),
                    source_node_id=source.node_id,
                    target_node_id=target.node_id,
                    relation_type="CITES",
                ),
            ),
        )


class _GraphFacts:
    def __init__(self) -> None:
        self.calls = []

    async def replace_revision(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _plugin(
    *,
    enable_llm_extraction: bool = False,
    chunk_selector=None,
    chunk_metadata_builder=None,
) -> RagPlugin:
    return RagPlugin(
        plugin_id="paper-citation-v1",
        metadata_type=PaperMetadata,
        ontology=Ontology(
            domain="paper",
            entity_specs={"paper": EntitySpec(category="paper", description="论文")},
            relation_specs={
                "CITES": RelationSpec(
                    relation_type="CITES",
                    description="引用",
                    allowed_sources=("paper",),
                    allowed_targets=("paper",),
                )
            },
        ),
        deterministic_producer=_CitationProducer(),
        enable_llm_extraction=enable_llm_extraction,
        chunk_selector=chunk_selector,
        chunk_metadata_builder=chunk_metadata_builder,
    )


def test_metadata_registry_only_decodes_registered_types() -> None:
    registry = RagPluginRegistry(plugins=[_plugin()])
    metadata = PaperMetadata(title="Source", reference_title="Target")

    codec = registry.document_metadata_codec
    assert codec.decode(codec.encode(metadata)) == metadata
    with pytest.raises(ValueError, match="unregistered"):
        codec.decode({"document_type": "unknown"})


def test_plugin_chunk_metadata_builder_is_registered_for_persistence() -> None:
    plugin = _plugin(chunk_metadata_builder=_PaperChunkMetadataBuilder())
    registry = RagPluginRegistry(plugins=[plugin])
    metadata = PaperChunkMetadata(
        section_role="abstract",
        is_graph_extraction_target=True,
    )

    codec = registry.doc_chunk_metadata_codec
    assert codec.decode(codec.encode(metadata)) == metadata


def test_plugin_registry_validates_unique_plugins_and_routes_metadata() -> None:
    plugin = _plugin(chunk_metadata_builder=_PaperChunkMetadataBuilder())
    registry = RagPluginRegistry(plugins=[plugin])

    assert registry.get(plugin.plugin_id) is plugin
    assert registry.match_document(
        PaperMetadata(title="Source", reference_title="Target")
    ) is plugin
    assert registry.match_document(GeneralDocumentMetadata()) is None
    with pytest.raises(ValueError, match="plugin ids"):
        RagPluginRegistry(plugins=[plugin, _plugin()])


def test_plugin_registry_rejects_duplicate_metadata_and_chunk_types() -> None:
    class _OtherPaperMetadata(DocumentMetadata):
        document_type: str = "paper"

    duplicate_metadata_plugin = RagPlugin(
        plugin_id="other-paper",
        metadata_type=_OtherPaperMetadata,
        ontology=Ontology(domain="other"),
    )
    with pytest.raises(ValueError, match="document metadata types"):
        RagPluginRegistry(plugins=[_plugin(), duplicate_metadata_plugin])

    class _OtherMetadata(DocumentMetadata):
        document_type: str = "other"

    class _OtherChunkMetadata(DocChunkMetadata):
        chunk_type: str = "paper"

    class _OtherChunkMetadataBuilder:
        doc_metadata_type = _OtherMetadata
        chunk_metadata_type = _OtherChunkMetadata

        def build_metadata(self, *, document, chunk) -> _OtherChunkMetadata:
            return _OtherChunkMetadata()

    duplicate_chunk_plugin = RagPlugin(
        plugin_id="other",
        metadata_type=_OtherMetadata,
        ontology=Ontology(domain="other"),
        chunk_metadata_builder=_OtherChunkMetadataBuilder(),
    )
    with pytest.raises(ValueError, match="chunk metadata types"):
        RagPluginRegistry(
            plugins=[
                _plugin(chunk_metadata_builder=_PaperChunkMetadataBuilder()),
                duplicate_chunk_plugin,
            ]
        )


def test_fact_text_marks_every_relationship_role() -> None:
    source = GraphSourceProjection(
        projection_id="projection",
        target_type="edge",
        target_id="edge",
        resource_id="paper",
        content_revision="paper@1#hash",
        evidence_ids=[],
        producer_id="paper-citation-v1",
        edge=GraphEdge(
            edge_id="edge",
            source_node_id="source",
            target_node_id="target",
            relation_type="CITES",
            description="Source cites Target",
        ),
        source_node_name="Source",
        target_node_name="Target",
    )

    assert source.get_fact_text() == (
        "来源实体: Source\n"
        "关系: CITES\n"
        "目标实体: Target\n"
        "事实说明: Source cites Target"
    )


@pytest.mark.asyncio
async def test_preparer_preserves_typed_metadata_and_same_revision_cannot_change_it() -> (
    None
):
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=MemoryDocumentVectors(),
        index_states=states,
    )
    preparer = DocumentPreparer(publication=publication, doc_chunks=chunks)
    metadata = PaperMetadata(title="Source", reference_title="Target")

    await preparer.prepare(
        resource_id="paper",
        document_version=1,
        markdown="# Source",
        metadata=metadata,
    )
    revision = next(iter(documents.documents))
    assert documents.documents[revision].metadata == metadata

    with pytest.raises(ValueError, match="metadata differs"):
        await documents.save_revision(
            replace(
                documents.documents[revision],
                metadata=PaperMetadata(title="Other", reference_title="Target"),
            )
        )


@pytest.mark.asyncio
async def test_paper_chunk_metadata_is_prepared_then_selects_llm_targets() -> None:
    """论文段落角色在准备期落库，图谱抽取只消费已确定的 metadata。"""
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=MemoryDocumentVectors(),
        index_states=states,
    )
    preparer = DocumentPreparer(
        publication=publication,
        doc_chunks=chunks,
        chunker_config=DocumentChunkerConfig(max_characters=800, chunk_overlap=100),
        chunk_metadata_builder=DocumentChunkMetadataBuilder(
            builders=[_PaperChunkMetadataBuilder()]
        ),
    )
    markdown = "# Abstract\n\n本文提出一种论文图谱方法。\n\n# Method\n\n方法细节。\n"

    await preparer.prepare(
        resource_id="paper",
        document_version=1,
        markdown=markdown,
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    content_revision = states.states["paper"].staged_content_revision
    prepared_chunks = await chunks.get_revision_chunks(
        resource_id="paper",
        content_revision=content_revision,
    )
    metadata_by_section = {
        chunk.section_path[-1]: chunk.metadata for chunk in prepared_chunks
    }
    plugin = _plugin(
        enable_llm_extraction=True,
        chunk_metadata_builder=_PaperChunkMetadataBuilder(),
        chunk_selector=lambda chunk: (
            isinstance(chunk.metadata, PaperChunkMetadata)
            and chunk.metadata.is_graph_extraction_target
        ),
    )

    assert metadata_by_section["Abstract"] == PaperChunkMetadata(
        section_role="abstract",
        is_graph_extraction_target=True,
    )
    assert metadata_by_section["Method"] == PaperChunkMetadata(
        section_role="method",
        is_graph_extraction_target=False,
    )
    assert [chunk.section_path[-1] for chunk in plugin.select_chunks(prepared_chunks)] == [
        "Abstract"
    ]


@pytest.mark.asyncio
async def test_deterministic_plugin_writes_facts_without_text_evidence() -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    item = replace(
        document(resource_id="paper", version=1, section_id="section"),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    chunk = chunk_for_document(item)
    await documents.save_revision(item)
    await chunks.save_revision([chunk])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugin_registry=RagPluginRegistry(plugins=[_plugin()]),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    result = await builder.build(resource_id="paper")

    assert result is not None
    assert (result.node_count, result.edge_count, result.evidence_count) == (2, 1, 0)
    assert len(facts.calls) == 1
    assert facts.calls[0]["evidences"] == []
    assert facts.calls[0]["nodes"][0].producer_id == "paper-citation-v1"


@pytest.mark.asyncio
async def test_chunk_selector_limits_llm_targets_but_keeps_full_shared_window(monkeypatch) -> None:
    item = replace(
        document(
            resource_id="paper",
            version=1,
            section_id="section",
            raw_content="selected context",
        ),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    selected = replace(
        chunk_for_document(item),
        chunk_id="selected",
        raw_text="selected",
        source_spans=[SourceSpan(0, 8)],
    )
    context = replace(
        selected,
        chunk_id="context",
        chunk_index=1,
        raw_text="context",
        source_spans=[SourceSpan(9, 16)],
    )
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    await documents.save_revision(item)
    await chunks.save_revision([selected, context])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    extracted_targets = []

    async def fake_extract_chunk(*args, **kwargs):
        extracted_targets.append((kwargs["chunk"].chunk_id, kwargs["chunks"]))
        return _GraphExtraction()

    monkeypatch.setattr(graph_fact_builder, "_extract_chunk", fake_extract_chunk)
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=_GraphFacts(),
        index_states=states,
        plugin_registry=RagPluginRegistry(
            plugins=[_plugin(
                enable_llm_extraction=True,
                chunk_selector=lambda chunk: chunk.chunk_id == "selected",
            )],
        ),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    await builder.build(resource_id="paper")

    assert [target[0] for target in extracted_targets] == ["selected"]
    assert [chunk.chunk_id for chunk in extracted_targets[0][1]] == ["selected", "context"]


@pytest.mark.asyncio
async def test_empty_chunk_selection_skips_llm_and_keeps_deterministic_facts(monkeypatch) -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    item = replace(
        document(resource_id="paper", version=1, section_id="section"),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    await documents.save_revision(item)
    await chunks.save_revision([chunk_for_document(item)])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    facts = _GraphFacts()

    async def unexpected_extract(*args, **kwargs):
        raise AssertionError("empty selection must not call the LLM")

    monkeypatch.setattr(graph_fact_builder, "_extract_chunk", unexpected_extract)
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugin_registry=RagPluginRegistry(
            plugins=[
                _plugin(enable_llm_extraction=True, chunk_selector=lambda chunk: False)
            ],
        ),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    result = await builder.build(resource_id="paper")

    assert result is not None
    assert (result.node_count, result.edge_count, result.evidence_count) == (2, 1, 0)
    assert len(facts.calls) == 1


def test_llm_quote_must_be_unique_inside_target_chunk() -> None:
    item = document(
        resource_id="resource",
        version=1,
        section_id="section",
        raw_content="Alpha is a method. Context Alpha is elsewhere.",
    )
    chunk = replace(
        chunk_for_document(item),
        raw_text="Alpha is a method.",
        source_spans=(SourceSpan(0, 18),),
    )
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    evidences = []
    evidence_ids_by_target = defaultdict(list)
    chunk_node_ids = defaultdict(list)
    extraction = _GraphExtraction(
        nodes=(
            _ExtractedNode(
                local_id="alpha",
                name="Alpha",
                category="paper",
                quote="Alpha",
            ),
        )
    )

    _collect_llm_facts(
        document=item,
        chunk=chunk,
        extraction=extraction,
        plugin=_plugin(),
        nodes=nodes,
        edges=edges,
        evidences=evidences,
        evidence_ids_by_target=evidence_ids_by_target,
        chunk_node_ids=chunk_node_ids,
    )

    assert len(nodes) == 1
    assert evidences[0].quote_text == "Alpha"
    assert evidences[0].source_spans == [SourceSpan(0, 5)]

    duplicate = _GraphExtraction(
        nodes=(
            _ExtractedNode(
                local_id="other",
                name="Other",
                category="paper",
                quote="Alpha",
            ),
        )
    )
    duplicate_chunk = replace(
        chunk,
        source_spans=(SourceSpan(0, len(item.raw_content)),),
    )
    _collect_llm_facts(
        document=item,
        chunk=duplicate_chunk,
        extraction=duplicate,
        plugin=_plugin(),
        nodes=nodes,
        edges=edges,
        evidences=evidences,
        evidence_ids_by_target=evidence_ids_by_target,
        chunk_node_ids=chunk_node_ids,
    )
    assert len(nodes) == 1


@pytest.mark.asyncio
async def test_general_document_skips_graph_build_without_model_call() -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    item = document(resource_id="general", version=1, section_id="section")
    await documents.save_revision(item)
    await chunks.save_revision([chunk_for_document(item)])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugin_registry=RagPluginRegistry(plugins=[_plugin()]),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    result = await builder.build(resource_id="general")

    assert result is not None
    assert result.node_count == result.edge_count == result.evidence_count == 0
    assert facts.calls == []


@pytest.mark.asyncio
async def test_active_switch_during_build_prevents_old_graph_write() -> None:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    item = replace(
        document(resource_id="paper", version=1, section_id="section"),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    await documents.save_revision(item)
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)

    class _ChangingChunks(MemoryDocChunks):
        async def get_revision_chunks(self, *, resource_id, content_revision):
            result = await super().get_revision_chunks(
                resource_id=resource_id,
                content_revision=content_revision,
            )
            states.states[resource_id] = ResourceIndexState(
                resource_id=resource_id,
                applied_content_revision="newer-revision",
            )
            return result

    chunks = _ChangingChunks()
    await chunks.save_revision([chunk_for_document(item)])
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugin_registry=RagPluginRegistry(plugins=[_plugin()]),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    with pytest.raises(RuntimeError, match="active revision changed"):
        await builder.build(resource_id="paper")
    assert facts.calls == []
