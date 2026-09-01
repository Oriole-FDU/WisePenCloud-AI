"""由插件驱动的 LLM 与确定性图谱事实生产。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, replace

import instructor
from common.utils.document import SourceSpan
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from rag_v3.application.document.indexing import _shared_window
from rag_v3.domain.graph import (
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    TextGraphEvidence,
    graph_edge_id,
    graph_evidence_id,
    graph_node_id,
)
from rag_v3.domain.models import DocChunk, Document
from rag_v3.domain.plugins import GraphPlugin
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.graph import GraphFactRepository
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository

_SYSTEM_PROMPT = """Extract only graph facts supported by the target chunk.
The shared window is context only. Every node and relation must quote exact text
from the target chunk, never from the shared window alone. Return no fact when
the target chunk does not support it."""


class _ExtractedNode(BaseModel):
    """Instructor 外部响应中的一个节点候选。"""

    model_config = ConfigDict(extra="forbid")

    local_id: str
    name: str
    category: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    quote: str


class _ExtractedEdge(BaseModel):
    """Instructor 外部响应中的一个关系候选。"""

    model_config = ConfigDict(extra="forbid")

    source_local_id: str
    target_local_id: str
    relation_type: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    quote: str


class _GraphExtraction(BaseModel):
    """一次目标 Chunk 抽取的结构化边界。"""

    model_config = ConfigDict(extra="forbid")

    nodes: tuple[_ExtractedNode, ...] = ()
    edges: tuple[_ExtractedEdge, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    """一次已发布 revision 图谱构建的实际产出计数。"""

    resource_id: str
    content_revision: str
    node_count: int
    edge_count: int
    evidence_count: int


class GraphFactBuilder:
    """构建已发布 revision 的 Mongo 图谱事实，不控制 active 指针。"""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        doc_chunks: DocChunkRepository,
        graph_facts: GraphFactRepository,
        index_states: ResourceIndexStateRepository,
        plugins: tuple[GraphPlugin, ...],
        openai_client: AsyncOpenAI,
        query_model: str,
        max_concurrency: int,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._graph_facts = graph_facts
        self._index_states = index_states
        self._plugins = plugins
        self._openai_client = openai_client
        self._query_model = query_model
        self._max_concurrency = max_concurrency

    async def build(self, *, resource_id: str) -> GraphBuildResult | None:
        """只补建当前 active revision；没有匹配插件时不调用模型也不写图。"""
        state = (await self._index_states.get_states([resource_id])).get(resource_id)
        if state is None or state.applied_content_revision is None:
            return None
        content_revision = state.applied_content_revision
        documents = await self._documents.get_revisions([(resource_id, content_revision)])
        document = documents.get((resource_id, content_revision))
        if document is None:
            raise ValueError("active document is missing")
        plugin = _matching_plugin(document, self._plugins)
        if plugin is None:
            return GraphBuildResult(resource_id, content_revision, 0, 0, 0)

        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        nodes, edges, evidences, chunk_node_ids = await self._produce(
            document=document,
            chunks=chunks,
            plugin=plugin,
        )

        # 模型调用期间 revision 可能已替换；旧图谱绝不能回写到当前资源视图。
        current = (await self._index_states.get_states([resource_id])).get(resource_id)
        if current is None or current.applied_content_revision != content_revision:
            raise RuntimeError("active revision changed during graph build")
        await self._graph_facts.replace_revision(
            resource_id=resource_id,
            content_revision=content_revision,
            nodes=nodes,
            edges=edges,
            evidences=evidences,
        )
        if chunk_node_ids:
            await self._doc_chunks.save_revision(
                [
                    replace(
                        chunk,
                        extracted_node_ids=tuple(
                            dict.fromkeys(chunk_node_ids.get(chunk.chunk_id, ()))
                        ),
                    )
                    for chunk in chunks
                ]
            )
        return GraphBuildResult(
            resource_id=resource_id,
            content_revision=content_revision,
            node_count=len(nodes),
            edge_count=len(edges),
            evidence_count=len(evidences),
        )

    async def _produce(
        self,
        *,
        document: Document,
        chunks: list[DocChunk],
        plugin: GraphPlugin,
    ) -> tuple[
        tuple[GraphNodeProjection, ...],
        tuple[GraphEdgeProjection, ...],
        tuple[TextGraphEvidence, ...],
        dict[str, list[str]],
    ]:
        deterministic_nodes, deterministic_edges = _deterministic_facts(document, plugin)
        llm_nodes: dict[str, GraphNode] = {}
        llm_edges: dict[str, GraphEdge] = {}
        evidence_ids_by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
        evidences: list[TextGraphEvidence] = []
        chunk_node_ids: dict[str, list[str]] = defaultdict(list)

        if plugin.enable_llm_extraction and chunks:
            semaphore = asyncio.Semaphore(self._max_concurrency)
            extracted = await asyncio.gather(
                *(
                    _extract_chunk(
                        self._openai_client,
                        model=self._query_model,
                        document=document,
                        chunks=chunks,
                        chunk=chunk,
                        semaphore=semaphore,
                    )
                    for chunk in chunks
                )
            )
            for chunk, extraction in zip(chunks, extracted, strict=True):
                _collect_llm_facts(
                    document=document,
                    chunk=chunk,
                    extraction=extraction,
                    plugin=plugin,
                    nodes=llm_nodes,
                    edges=llm_edges,
                    evidences=evidences,
                    evidence_ids_by_target=evidence_ids_by_target,
                    chunk_node_ids=chunk_node_ids,
                )

        node_projections = [
            GraphNodeProjection(
                node=node,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                producer_id=plugin.plugin_id,
            )
            for node in deterministic_nodes.values()
        ]
        edge_projections = [
            GraphEdgeProjection(
                edge=edge,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                producer_id=plugin.plugin_id,
            )
            for edge in deterministic_edges.values()
        ]
        node_projections.extend(
            GraphNodeProjection(
                node=node,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                evidence_ids=tuple(dict.fromkeys(evidence_ids_by_target[("node", node_id)])),
            )
            for node_id, node in llm_nodes.items()
        )
        edge_projections.extend(
            GraphEdgeProjection(
                edge=edge,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                evidence_ids=tuple(dict.fromkeys(evidence_ids_by_target[("edge", edge_id)])),
            )
            for edge_id, edge in llm_edges.items()
        )
        return (
            tuple(node_projections),
            tuple(edge_projections),
            tuple(evidences),
            chunk_node_ids,
        )


def _matching_plugin(document: Document, plugins: tuple[GraphPlugin, ...]) -> GraphPlugin | None:
    matches = [plugin for plugin in plugins if plugin.matches(document.metadata)]
    if len(matches) > 1:
        raise ValueError("document metadata matches multiple graph plugins")
    return matches[0] if matches else None


def _deterministic_facts(
    document: Document,
    plugin: GraphPlugin,
) -> tuple[dict[str, GraphNode], dict[str, GraphEdge]]:
    if plugin.deterministic_producer is None:
        return {}, {}
    nodes, edges = plugin.deterministic_producer.produce(document)
    nodes_by_id = {node.node_id: node for node in nodes}
    for node in nodes_by_id.values():
        plugin.ontology.validate_node(node)
    for edge in edges:
        plugin.ontology.validate_edge(edge, nodes_by_id)
    return nodes_by_id, {edge.edge_id: edge for edge in edges}


async def _extract_chunk(
    openai_client: AsyncOpenAI,
    *,
    model: str,
    document: Document,
    chunks: list[DocChunk],
    chunk: DocChunk,
    semaphore: asyncio.Semaphore,
) -> _GraphExtraction:
    """复用 P1-B 的窗口，并限制 Evidence 只从 target chunk 产生。"""
    client = instructor.from_openai(openai_client)
    shared_window = _shared_window(document, chunks, chunk)
    async with semaphore:
        return await client.chat.completions.create(
            model=model,
            response_model=_GraphExtraction,
            max_retries=1,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "\n\n".join(
                        (
                            "<shared_window>\n" + shared_window + "\n</shared_window>",
                            "<target_chunk>\n" + chunk.raw_text + "\n</target_chunk>",
                        )
                    ),
                },
            ],
        )


def _collect_llm_facts(
    *,
    document: Document,
    chunk: DocChunk,
    extraction: _GraphExtraction,
    plugin: GraphPlugin,
    nodes: dict[str, GraphNode],
    edges: dict[str, GraphEdge],
    evidences: list[TextGraphEvidence],
    evidence_ids_by_target: dict[tuple[str, str], list[str]],
    chunk_node_ids: dict[str, list[str]],
) -> None:
    local_nodes: dict[str, GraphNode] = {}
    for extracted in extraction.nodes:
        spec = plugin.ontology.entity_specs.get(extracted.category)
        if spec is None:
            continue
        node = GraphNode(
            node_id=graph_node_id(category=extracted.category, name=extracted.name),
            name=extracted.name,
            node_type=spec.default_node_type,
            category=extracted.category,
            description=extracted.description,
            aliases=tuple(dict.fromkeys(extracted.aliases)),
        )
        span = _locate_quote(document, chunk, extracted.quote)
        if span is None:
            continue
        plugin.ontology.validate_node(node)
        local_nodes[extracted.local_id] = node
        nodes[node.node_id] = node
        evidence = _text_evidence(document, chunk, "node", node.node_id, span, extracted.quote)
        evidences.append(evidence)
        evidence_ids_by_target[("node", node.node_id)].append(evidence.evidence_id)
        chunk_node_ids[chunk.chunk_id].append(node.node_id)

    for extracted in extraction.edges:
        source = local_nodes.get(extracted.source_local_id)
        target = local_nodes.get(extracted.target_local_id)
        if source is None or target is None:
            continue
        edge = GraphEdge(
            edge_id=graph_edge_id(
                source_node_id=source.node_id,
                relation_type=extracted.relation_type,
                target_node_id=target.node_id,
            ),
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            relation_type=extracted.relation_type,
            description=extracted.description,
            keywords=tuple(dict.fromkeys(extracted.keywords)),
        )
        span = _locate_quote(document, chunk, extracted.quote)
        if span is None:
            continue
        try:
            plugin.ontology.validate_edge(edge, local_nodes_by_id(local_nodes))
        except ValueError:
            continue
        edges[edge.edge_id] = edge
        evidence = _text_evidence(document, chunk, "edge", edge.edge_id, span, extracted.quote)
        evidences.append(evidence)
        evidence_ids_by_target[("edge", edge.edge_id)].append(evidence.evidence_id)


def local_nodes_by_id(local_nodes: dict[str, GraphNode]) -> dict[str, GraphNode]:
    return {node.node_id: node for node in local_nodes.values()}


def _locate_quote(document: Document, chunk: DocChunk, quote: str) -> SourceSpan | None:
    """只接受目标 Chunk 完整 block span 内唯一出现的精确 quote。"""
    if not quote:
        return None
    locations: list[SourceSpan] = []
    for allowed in chunk.source_spans:
        start = allowed.start_offset
        while True:
            found = document.raw_content.find(quote, start, allowed.end_offset)
            if found < 0:
                break
            end = found + len(quote)
            if end <= allowed.end_offset:
                locations.append(SourceSpan(found, end))
            start = found + 1
    return locations[0] if len(locations) == 1 else None


def _text_evidence(
    document: Document,
    chunk: DocChunk,
    target_type: str,
    target_id: str,
    span: SourceSpan,
    quote_text: str,
) -> TextGraphEvidence:
    evidence_id = graph_evidence_id(
        target_type=target_type,  # type: ignore[arg-type]
        target_id=target_id,
        chunk_id=chunk.chunk_id,
        span=span,
        quote_text=quote_text,
    )
    return TextGraphEvidence(
        evidence_id=evidence_id,
        target_type=target_type,  # type: ignore[arg-type]
        target_id=target_id,
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        section_id=chunk.section_id,
        chunk_id=chunk.chunk_id,
        source_spans=(span,),
        quote_text=quote_text,
    )
