"""由插件驱动的 LLM 与确定性图谱事实生产。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Literal

import instructor
from common.utils.document import SourceSpan
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rag.application.document.indexing import _shared_window
from rag.application.document.models import DocChunk, Document
from rag.application.graph.models import (
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    TextGraphEvidence,
    graph_edge_id,
    graph_evidence_id,
    graph_node_id,
)
from rag.application.plugins.core import RagPlugin
from rag.application.plugins.core.registry import RagPluginRegistry
from rag.domain.repositories.doc_chunks import DocChunkRepository
from rag.domain.repositories.documents import DocumentRepository
from rag.domain.repositories.graph_fact import GraphFactRepository
from rag.domain.repositories.index_state import ResourceIndexStateRepository

# --- LLM 抽取提示词与 Schema ---

_SYSTEM_PROMPT = """Extract verifiable knowledge graph nodes and relations strictly grounded in the <target_chunk>.
The <shared_window> provides context only; never extract facts that exist solely in the <shared_window>.

Core Extraction Rules:
1. Grounding & Zero Hallucination: Extract a fact ONLY if it is explicitly stated in <target_chunk>. Return empty lists if no valid facts exist.
2. Referential Integrity: Assign each extracted node a unique sequential ID (`n1`, `n2`, `n3`...). In relations, `source_local_id` and `target_local_id` MUST strictly reference an existing node's `local_id`.
3. Verbatim Evidence (`quote`): For every node and edge, `quote` MUST be an exact, continuous, and uninterrupted substring copied character-for-character from <target_chunk>. Do not paraphrase, reformat, or alter whitespace."""


class _ExtractedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str = Field(
        description="Sequential local identifier (e.g., 'n1', 'n2') assigned in order of appearance."
    )
    name: str = Field(
        description="Canonical name of the entity as explicitly mentioned in the target chunk."
    )
    category: str = Field(
        description="Standard entity category/type conforming to the domain ontology."
    )
    description: str = Field(
        default="",
        description="Concise, factual summary of the entity based solely on the target chunk.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names, acronyms, or synonyms explicitly mentioned in the target chunk.",
    )
    quote: str = Field(
        description="Exact, uninterrupted verbatim substring copied from the target chunk supporting this entity."
    )


class _ExtractedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_local_id: str = Field(
        description="The `local_id` of the source node (e.g., 'n1'). Must match an extracted node."
    )
    target_local_id: str = Field(
        description="The `local_id` of the target node (e.g., 'n2'). Must match an extracted node."
    )
    relation_type: str = Field(
        description="Predicate or relationship type conforming to the domain ontology."
    )
    description: str = Field(
        default="",
        description="Concise description explaining the specific relationship between the two entities.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="High-value domain terms, action verbs, and search keywords characterizing this relation.",
    )
    quote: str = Field(
        description="Exact, uninterrupted verbatim substring copied from the target chunk supporting this relation."
    )


class _GraphExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[_ExtractedNode] = Field(
        default_factory=list,
        description="List of entities explicitly identified and grounded in the target chunk.",
    )
    edges: list[_ExtractedEdge] = Field(
        default_factory=list,
        description="List of directed relationships connecting the extracted nodes.",
    )


# --- 构建结果值对象 ---

@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    """一次已发布 revision 图谱构建的实际产出计数。"""

    resource_id: str
    content_revision: str
    node_count: int
    edge_count: int
    evidence_count: int


# --- 图谱事实构建器 ---

class GraphFactBuilder:
    """构建已发布 revision 的 Mongo 图谱事实，不控制 active 指针。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        documents: DocumentRepository,
        doc_chunks: DocChunkRepository,
        graph_facts: GraphFactRepository,
        index_states: ResourceIndexStateRepository,
        plugin_registry: RagPluginRegistry,
        openai_client: AsyncOpenAI,
        query_model: str,
        max_concurrency: int,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._enabled = enabled
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._graph_facts = graph_facts
        self._index_states = index_states
        self._plugin_registry = plugin_registry
        self._openai_client = openai_client
        self._instructor_client = (
            instructor.from_openai(openai_client) if openai_client is not None else None
        )
        self._query_model = query_model
        self._max_concurrency = max_concurrency

    async def build(self, *, resource_id: str) -> GraphBuildResult | None:
        """只补建当前 active revision；没有匹配插件时不调用模型也不写图。"""
        if not self._enabled:
            return None

        # 获取当前已发布的 revision
        state = (await self._index_states.get_states([resource_id])).get(resource_id)
        if state is None or state.applied_content_revision is None:
            return None
        content_revision = state.applied_content_revision

        # 读取文档和匹配插件
        documents = await self._documents.get_revisions(
            [(resource_id, content_revision)]
        )
        document = documents.get((resource_id, content_revision))
        if document is None:
            raise ValueError("active document is missing")
        # 插件类型分发
        plugin = self._plugin_registry.match_document(document.metadata)
        if plugin is None:
            return GraphBuildResult(resource_id, content_revision, 0, 0, 0)

        # 获取 chunk 并生产事实
        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        nodes, edges, evidences, chunk_node_ids = await self._produce(
            document=document,
            chunks=chunks,
            plugin=plugin,
        )

        # 模型调用期间 revision 可能已替换；再次校验避免回写旧数据
        current = (await self._index_states.get_states([resource_id])).get(resource_id)
        if current is None or current.applied_content_revision != content_revision:
            raise RuntimeError("active revision changed during graph build")

        # 原子替换该 revision 的全部图谱事实
        await self._graph_facts.replace_revision(
            resource_id=resource_id,
            content_revision=content_revision,
            nodes=nodes,
            edges=edges,
            evidences=evidences,
        )

        # 回写每个 chunk 的 extracted_node_ids 供检索使用
        if chunk_node_ids:
            await self._doc_chunks.save_revision(
                [
                    replace(
                        chunk,
                        extracted_node_ids=list(
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
        plugin: RagPlugin,
    ) -> tuple[
        list[GraphNodeProjection],
        list[GraphEdgeProjection],
        list[TextGraphEvidence],
        dict[str, list[str]],
    ]:
        """生产节点、边、证据以及 chunk→节点ID 映射。"""
        # 1. 确定性规则事实（由插件提供）
        deterministic_nodes, deterministic_edges = _deterministic_facts(
            document, plugin
        )
        filter_values = plugin.filter_values(document)

        # 2. 准备 LLM 抽取的容器
        llm_nodes: dict[str, GraphNode] = {}
        llm_edges: dict[str, GraphEdge] = {}
        evidence_ids_by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
        evidences: list[TextGraphEvidence] = []
        chunk_node_ids: dict[str, list[str]] = defaultdict(list)

        # 3. LLM 抽取（仅在插件启用且有 chunk 时进行）
        selected_chunks = plugin.select_chunks(chunks)
        if plugin.enable_llm_extraction and selected_chunks:
            semaphore = asyncio.Semaphore(self._max_concurrency)
            chunk_indices = {
                chunk.chunk_id: index for index, chunk in enumerate(chunks)
            }
            extracted = await asyncio.gather(
                *(
                    _extract_chunk(
                        self._instructor_client,
                        model=self._query_model,
                        document=document,
                        chunks=chunks,
                        chunk_indices=chunk_indices,
                        chunk=chunk,
                        semaphore=semaphore,
                    )
                    for chunk in selected_chunks
                )
            )
            # 收集各 chunk 的抽取结果，合并相同逻辑节点/边并生成证据
            for chunk, extraction in zip(selected_chunks, extracted, strict=True):
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

        # 4. 构建确定性投影（producer_id 为插件 ID）
        node_projections = [
            GraphNodeProjection(
                node=node,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                producer_id=plugin.plugin_id,
                filter_values=filter_values,
            )
            for node in deterministic_nodes.values()
        ]
        edge_projections = [
            GraphEdgeProjection(
                edge=edge,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                producer_id=plugin.plugin_id,
                filter_values=filter_values,
            )
            for edge in deterministic_edges.values()
        ]

        # 5. 构建 LLM 投影（附带 evidence_ids）
        node_projections.extend(
            GraphNodeProjection(
                node=node,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                evidence_ids=list(
                    dict.fromkeys(evidence_ids_by_target[("node", node_id)])
                ),
                filter_values=filter_values,
            )
            for node_id, node in llm_nodes.items()
        )
        edge_projections.extend(
            GraphEdgeProjection(
                edge=edge,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                evidence_ids=list(
                    dict.fromkeys(evidence_ids_by_target[("edge", edge_id)])
                ),
                filter_values=filter_values,
            )
            for edge_id, edge in llm_edges.items()
        )

        return (
            node_projections,
            edge_projections,
            evidences,
            dict(chunk_node_ids),
        )


# --- 辅助函数 ---

def _deterministic_facts(
    document: Document,
    plugin: RagPlugin,
) -> tuple[dict[str, GraphNode], dict[str, GraphEdge]]:
    """运行插件的确定性生成器，并做本体校验。"""
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
    instructor_client,
    *,
    model: str,
    document: Document,
    chunks: list[DocChunk],
    chunk_indices: dict[str, int],
    chunk: DocChunk,
    semaphore: asyncio.Semaphore,
) -> _GraphExtraction:
    """对单个 chunk 调用 LLM 抽取，使用共享窗口提供上下文但限制证据只来自 target chunk。"""
    shared_window = _shared_window(document, chunks, chunk, chunk_indices)
    async with semaphore:
        return await instructor_client.chat.completions.create(
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
    plugin: RagPlugin,
    nodes: dict[str, GraphNode],
    edges: dict[str, GraphEdge],
    evidences: list[TextGraphEvidence],
    evidence_ids_by_target: dict[tuple[str, str], list[str]],
    chunk_node_ids: dict[str, list[str]],
) -> None:
    """将一个 chunk 的抽取结果合并到全局容器中，生成证据并去重。"""
    local_nodes: dict[str, GraphNode] = {}

    # 处理节点
    for extracted in extraction.nodes:
        spec = plugin.ontology.entity_specs.get(extracted.category)
        if spec is None:
            continue
        node = GraphNode(
            node_id=graph_node_id(category=extracted.category, name=extracted.name),
            name=extracted.name,
            node_type=spec.node_type,
            category=extracted.category,
            description=extracted.description,
            aliases=list(dict.fromkeys(extracted.aliases)),
        )
        span = _locate_quote(document, chunk, extracted.quote)
        if span is None:
            continue
        plugin.ontology.validate_node(node)

        # 合并同一逻辑节点的跨 chunk 描述和别名
        previous = nodes.get(node.node_id)
        if previous is not None:
            node = GraphNode(
                node_id=node.node_id,
                name=node.name,
                node_type=node.node_type,
                category=node.category,
                description=max((previous.description, node.description), key=len),
                aliases=list(dict.fromkeys([*previous.aliases, *node.aliases])),
                extra_meta={**previous.extra_meta, **node.extra_meta},
            )
        local_nodes[extracted.local_id] = node
        nodes[node.node_id] = node

        evidence = _text_evidence(
            document, chunk, "node", node.node_id, span, extracted.quote
        )
        evidences.append(evidence)
        evidence_ids_by_target[("node", node.node_id)].append(evidence.evidence_id)
        chunk_node_ids[chunk.chunk_id].append(node.node_id)

    # 处理边
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
            keywords=list(dict.fromkeys(extracted.keywords)),
        )
        span = _locate_quote(document, chunk, extracted.quote)
        if span is None:
            continue
        try:
            plugin.ontology.validate_edge(
                edge, {node.node_id: node for node in local_nodes.values()}
            )
        except ValueError:
            continue

        # 合并同一逻辑边的跨 chunk 描述和关键词
        previous = edges.get(edge.edge_id)
        if previous is not None:
            edge = GraphEdge(
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation_type=edge.relation_type,
                description=max((previous.description, edge.description), key=len),
                keywords=list(dict.fromkeys([*previous.keywords, *edge.keywords])),
                extra_meta={**previous.extra_meta, **edge.extra_meta},
            )
        edges[edge.edge_id] = edge

        evidence = _text_evidence(
            document, chunk, "edge", edge.edge_id, span, extracted.quote
        )
        evidences.append(evidence)
        evidence_ids_by_target[("edge", edge.edge_id)].append(evidence.evidence_id)


def _locate_quote(document: Document, chunk: DocChunk, quote: str) -> SourceSpan | None:
    """在目标 chunk 的允许偏移范围内查找 quote，要求唯一命中。"""
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
    target_type: Literal['node', 'edge'],
    target_id: str,
    span: SourceSpan,
    quote_text: str,
) -> TextGraphEvidence:
    """构造证据对象并生成稳定 evidence_id。"""
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
        source_spans=[span],
        quote_text=quote_text,
    )
