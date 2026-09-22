"""由插件驱动的 LLM 与确定性图谱事实生产。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Annotated

import instructor
from common.logger import info, warn
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rag.application.document.context import build_inline_document_context
from rag.application.document.models import DocChunk, Document
from rag.application.graph.models import (
    GraphChunkSource,
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    graph_chunk_source_id,
    graph_edge_id,
    graph_node_id,
)
from rag.application.plugins.core import RagPlugin
from rag.application.plugins.core.registry import RagPluginRegistry
from rag.domain.repositories.doc_chunks import DocChunkRepository
from rag.domain.repositories.documents import DocumentRepository
from rag.domain.repositories.graph_fact import GraphFactRepository
from rag.domain.repositories.index_state import ResourceIndexStateRepository


# Extraction limits

_MAX_NODES_PER_CHUNK = 12
_MAX_EDGES_PER_CHUNK = 24
_MAX_NAME_LENGTH = 128
_MAX_DESCRIPTION_LENGTH = 512
_MAX_KEYWORDS_PER_EDGE = 8
_MAX_KEYWORD_LENGTH = 64
_MAX_MERGED_DESCRIPTION_LENGTH = 512  # 跨 chunk 合并后的截断上限
_Keyword = Annotated[str, Field(max_length=_MAX_KEYWORD_LENGTH)]


# LLM system prompt

_SYSTEM_PROMPT = """Extract verifiable knowledge graph nodes and relations strictly grounded in <target_chunk>.
The <context_chunk> elements provide context only; never extract facts that exist solely in them.

Rules:
1. Extract only facts explicitly supported by <target_chunk>; return empty lists when none exist.
2. Edge endpoints must use the exact names of nodes returned in the same response.
3. Use only entity categories and relation types from the supplied ontology. Never invent labels.
4. Do not create nodes or relations just to satisfy an ontology constraint.
"""


# LLM response schema

class _ExtractedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=_MAX_NAME_LENGTH)
    category: str
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_LENGTH)


class _ExtractedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entity: str = Field(max_length=_MAX_NAME_LENGTH)
    target_entity: str = Field(max_length=_MAX_NAME_LENGTH)
    relation_type: str
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_LENGTH)
    keywords: list[_Keyword] = Field(default_factory=list, max_length=_MAX_KEYWORDS_PER_EDGE)


class _RepairedEdge(_ExtractedEdge):
    """边修复响应；只允许重写当前边，不允许引入新节点。"""


class _GraphExtraction(BaseModel):
    """单个 chunk 的完整抽取结果。"""

    model_config = ConfigDict(extra="forbid")

    nodes: list[_ExtractedNode] = Field(default_factory=list, max_length=_MAX_NODES_PER_CHUNK)
    edges: list[_ExtractedEdge] = Field(default_factory=list, max_length=_MAX_EDGES_PER_CHUNK)

# Public result type

@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    """一次 revision 图谱构建的产出摘要，供调用方做日志或监控。"""

    resource_id: str
    content_revision: str
    node_count: int
    edge_count: int
    source_count: int


# Main builder

class GraphFactBuilder:
    """
    为指定 resource 的已发布 revision 生成 Mongo 图谱事实。

    职责边界：只负责生产并写入 ``graph_facts`` 与 ``doc_chunks``；
    active 指针的切换由上层调用方决定，本类不干预。
    """

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
        llm_semaphore: asyncio.Semaphore,
    ) -> None:
        self._enabled = enabled
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._graph_facts = graph_facts
        self._index_states = index_states
        self._plugin_registry = plugin_registry
        self._instructor_client = (
            instructor.from_openai(openai_client) if openai_client is not None else None
        )
        self._query_model = query_model
        self._llm_semaphore = llm_semaphore

    async def build(self, *, resource_id: str) -> GraphBuildResult | None:
        """
        构建并持久化指定 resource 当前 applied revision 的图谱事实。

        若功能未启用或 revision 缺失则返回 ``None``；
        若构建期间 revision 被替换则抛出 ``RuntimeError``。
        """
        if not self._enabled:
            return None

        state = (await self._index_states.get_states([resource_id])).get(resource_id)
        if state is None or state.applied_content_revision is None:
            return None
        content_revision = state.applied_content_revision

        documents = await self._documents.get_revisions([(resource_id, content_revision)])
        document = documents.get((resource_id, content_revision))
        if document is None:
            raise ValueError("active document is missing")

        plugin = self._plugin_registry.match_document(document.metadata)
        if plugin is None:
            return GraphBuildResult(resource_id, content_revision, 0, 0, 0)

        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=resource_id, content_revision=content_revision
        )
        nodes, edges, sources, chunk_node_ids = await self._produce(
            document=document, chunks=chunks, plugin=plugin
        )

        # 写入前二次校验 revision 未被抢占
        current = (await self._index_states.get_states([resource_id])).get(resource_id)
        if current is None or current.applied_content_revision != content_revision:
            raise RuntimeError("active revision changed during graph build")

        await self._graph_facts.replace_revision(
            resource_id=resource_id,
            content_revision=content_revision,
            nodes=nodes,
            edges=edges,
            sources=sources,
        )
        # 空列表也必须回写，清理上一次构建遗留的 chunk 节点引用
        await self._doc_chunks.save_revision(
            [
                replace(
                    chunk,
                    extracted_node_ids=list(dict.fromkeys(chunk_node_ids.get(chunk.chunk_id, ()))),
                )
                for chunk in chunks
            ]
        )
        return GraphBuildResult(
            resource_id=resource_id,
            content_revision=content_revision,
            node_count=len(nodes),
            edge_count=len(edges),
            source_count=len(sources),
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
        list[GraphChunkSource],
        dict[str, list[str]],
    ]:
        """合并确定性事实与 LLM 抽取结果，返回可直接写库的 projection 列表。"""
        deterministic_nodes, deterministic_edges = _deterministic_facts(document, plugin)
        filter_values = plugin.filter_values(document)

        # LLM 抽取结果的中间聚合容器
        llm_nodes: dict[str, GraphNode] = {}
        llm_edges: dict[str, GraphEdge] = {}
        source_ids_by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
        sources: list[GraphChunkSource] = []
        chunk_node_ids: dict[str, list[str]] = defaultdict(list)

        selected_chunks = plugin.select_chunks(chunks)
        if plugin.enable_llm_extraction and selected_chunks:
            if self._instructor_client is None:
                raise RuntimeError("LLM extraction is enabled but the Instructor client is missing")

            # 并发抽取所有 chunk，受 semaphore 限流
            extracted = await asyncio.gather(
                *(
                    _extract_chunk(
                        self._instructor_client,
                        model=self._query_model,
                        document=document,
                        chunks=chunks,
                        chunk=chunk,
                        ontology=plugin.ontology,
                        semaphore=self._llm_semaphore,
                    )
                    for chunk in selected_chunks
                )
            )
            for chunk, extraction in zip(selected_chunks, extracted, strict=True):
                await _collect_llm_facts(
                    document=document,
                    chunk=chunk,
                    extraction=extraction,
                    plugin=plugin,
                    nodes=llm_nodes,
                    edges=llm_edges,
                    sources=sources,
                    source_ids_by_target=source_ids_by_target,
                    chunk_node_ids=chunk_node_ids,
                    instructor_client=self._instructor_client,
                    model=self._query_model,
                    semaphore=self._llm_semaphore,
                )

        # 确定性节点不附带 source_ids（无 chunk 溯源），放在列表前段
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
        # LLM 节点/边追加至尾部，保持确定性在前
        node_projections.extend(
            GraphNodeProjection(
                node=node,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                source_ids=list(dict.fromkeys(source_ids_by_target[("node", node_id)])),
                filter_values=filter_values,
            )
            for node_id, node in llm_nodes.items()
        )
        edge_projections.extend(
            GraphEdgeProjection(
                edge=edge,
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                source_ids=list(dict.fromkeys(source_ids_by_target[("edge", edge_id)])),
                filter_values=filter_values,
            )
            for edge_id, edge in llm_edges.items()
        )
        info(
            "graph build facts",
            resource_id=document.resource_id,
            content_revision=document.revision.content_revision,
            node_count=len(node_projections),
            edge_count=len(edge_projections),
            source_count=len(sources),
        )
        sources = list(dict((source.source_id, source) for source in sources).values())
        return node_projections, edge_projections, sources, dict(chunk_node_ids)


# ── 确定性事实生产 ────────────────────────────────────────────────────────────

def _deterministic_facts(
    document: Document, plugin: RagPlugin
) -> tuple[dict[str, GraphNode], dict[str, GraphEdge]]:
    """调用插件的确定性生产器，校验后返回 {id → entity} 字典。"""
    if plugin.deterministic_producer is None:
        return {}, {}
    nodes, edges = plugin.deterministic_producer.produce(document)
    nodes_by_id = {node.node_id: node for node in nodes}
    for node in nodes_by_id.values():
        plugin.ontology.validate_node(node)
    for edge in edges:
        plugin.ontology.validate_edge(edge, nodes_by_id)
    return nodes_by_id, {edge.edge_id: edge for edge in edges}


# ── LLM 抽取（ontology 渲染 / chunk 抽取 / 边修复） ──────────────────────────

def _render_ontology(ontology) -> str:
    """将 ontology 对象序列化为 LLM 可读的文本块。"""
    lines = [
        f"Ontology domain: {ontology.domain}",
        f"Ontology description: {ontology.description}",
        "Entity categories:",
    ]
    for category, spec in ontology.entity_specs.items():
        lines.append(f"- {category}: {spec.description}")
    lines.append("Relation types:")
    for relation, spec in ontology.relation_specs.items():
        sources = ", ".join(spec.allowed_sources) or "any category"
        targets = ", ".join(spec.allowed_targets) or "any category"
        lines.append(f"- {relation}: {spec.description}; source={sources}; target={targets}")
    return "\n".join(lines)


async def _extract_chunk(
    instructor_client,
    *,
    model: str,
    document: Document,
    chunks: list[DocChunk],
    chunk: DocChunk,
    ontology,
    semaphore: asyncio.Semaphore,
) -> _GraphExtraction:
    """对单个 chunk 发起 LLM 结构化抽取，受 semaphore 并发限流。"""
    document_context = build_inline_document_context(document, chunks, chunk)
    async with semaphore:
        return await instructor_client.chat.completions.create(
            model=model,
            response_model=_GraphExtraction,
            max_tokens=4096,
            max_retries=1,  # Instructor 内置格式校验重试，失败一次后原样上报
            messages=[
                {"role": "system", "content": f"{_SYSTEM_PROMPT}\n\n{_render_ontology(ontology)}"},
                {"role": "user", "content": document_context},
            ],
        )


async def _repair_edge(
    *,
    instructor_client,
    model: str,
    ontology,
    node_names: list[str],
    node_index: dict[str, GraphNode],
    original: _ExtractedEdge,
    semaphore: asyncio.Semaphore,
) -> GraphEdge | None:
    """
    对无效边发起一次 LLM 局部修复。

    只允许在已有节点名与本体类型范围内重写端点/类型；
    无法修复时返回 ``None``，不再重试（避免放大延迟）。
    """
    prompt = (
        "Repair this one invalid graph edge. Return null when it cannot be repaired. "
        "You may only use the supplied node names and ontology; do not create nodes.\n"
        f"Allowed node names: {node_names}\n"
        f"Ontology:\n{_render_ontology(ontology)}\n"
        f"Original edge: {original.model_dump_json()}"
    )
    async with semaphore:
        repaired = await instructor_client.chat.completions.create(
            model=model,
            response_model=_RepairedEdge,
            max_tokens=1024,
            max_retries=0,
            messages=[{"role": "user", "content": prompt}],
        )

    # 校验修复结果的端点确实落在已知节点名内
    source = _normalize_name(repaired.source_entity)
    target = _normalize_name(repaired.target_entity)
    allowed = {_normalize_name(n) for n in node_names}
    if source not in allowed or target not in allowed:
        return None

    source_node = node_index[source]
    target_node = node_index[target]
    return GraphEdge(
        edge_id=graph_edge_id(
            source_node_id=source_node.node_id,
            relation_type=repaired.relation_type,
            target_node_id=target_node.node_id,
        ),
        source_node_id=source_node.node_id,
        target_node_id=target_node.node_id,
        relation_type=repaired.relation_type,
        description=repaired.description,
        keywords=list(dict.fromkeys(repaired.keywords)),
    )


# ── 事实收集与合并 ────────────────────────────────────────────────────────────

def _normalize_name(value: str) -> str:
    """规范化实体名：去首尾空格、折叠内部空白、全部小写，用于去重匹配。"""
    return " ".join(value.strip().casefold().split())


def _bounded_description(previous: str, current: str) -> str:
    """两段描述都未超限时取较长的；否则取较短的以保留相对完整的语义，再截断兜底。"""
    if len(previous) <= _MAX_MERGED_DESCRIPTION_LENGTH and len(current) <= _MAX_MERGED_DESCRIPTION_LENGTH:
        return max(previous, current, key=len)
    return min(previous, current, key=len)[:_MAX_MERGED_DESCRIPTION_LENGTH]


async def _collect_llm_facts(
    *,
    document: Document,
    chunk: DocChunk,
    extraction: _GraphExtraction,
    plugin: RagPlugin,
    nodes: dict[str, GraphNode],
    edges: dict[str, GraphEdge],
    sources: list[GraphChunkSource],
    source_ids_by_target: dict[tuple[str, str], list[str]],
    chunk_node_ids: dict[str, list[str]],
    instructor_client,
    model: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """
    将单个 chunk 的 LLM 抽取结果合并到全局累积容器。

    节点：校验 → 跨 chunk 去重合并 → 记录 source。
    边：先尝试直接解析，失败则发起一次 LLM 修复；修复仍失败则丢弃并警告。
    """
    # ---------- 节点 ----------
    local_nodes: dict[str, GraphNode] = {}  # 规范化名 → GraphNode，仅本 chunk 可见
    for extracted in extraction.nodes:
        spec = plugin.ontology.entity_specs.get(extracted.category)
        if spec is None:
            warn(
                "discard graph node",
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                chunk_id=chunk.chunk_id,
                reason="unknown_category",
                category=extracted.category,
            )
            continue

        name = " ".join(extracted.name.strip().split())
        if not name:
            warn(
                "discard graph node",
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                chunk_id=chunk.chunk_id,
                reason="empty_name",
            )
            continue

        node = GraphNode(
            node_id=graph_node_id(category=extracted.category, name=name),
            name=name,
            node_type=spec.node_type,
            category=extracted.category,
            description=extracted.description,
        )
        plugin.ontology.validate_node(node)

        # 跨 chunk 合并：取较长描述，extra_meta 后者覆盖前者
        previous = nodes.get(node.node_id)
        if previous is not None:
            node = node.model_copy(
                update={
                    "description": _bounded_description(previous.description, node.description),
                    "extra_meta": {**previous.extra_meta, **node.extra_meta},
                }
            )

        local_nodes[_normalize_name(name)] = node
        nodes[node.node_id] = node

        source = _chunk_source(document, chunk, "node", node.node_id)
        sources.append(source)
        source_ids_by_target[("node", node.node_id)].append(source.source_id)
        chunk_node_ids[chunk.chunk_id].append(node.node_id)

    # ---------- 边 ----------
    known_nodes = {node.node_id: node for node in local_nodes.values()}
    for extracted in extraction.edges:
        edge = _edge_from_extracted(extracted, local_nodes)
        error = None if edge is None else _validate_edge(edge, plugin, known_nodes)

        if edge is None or error is not None:
            # 端点未命中或本体校验失败，触发一次 LLM 修复
            edge = await _repair_edge(
                instructor_client=instructor_client,
                model=model,
                ontology=plugin.ontology,
                node_names=[node.name for node in local_nodes.values()],
                node_index=local_nodes,
                original=extracted,
                semaphore=semaphore,
            )
            if edge is not None:
                error = _validate_edge(edge, plugin, known_nodes)

        if edge is None or error is not None:
            warn(
                "discard graph edge",
                resource_id=document.resource_id,
                content_revision=document.revision.content_revision,
                chunk_id=chunk.chunk_id,
                reason=str(error or "endpoint_missing"),
                source=extracted.source_entity,
                target=extracted.target_entity,
            )
            continue

        _merge_edge(edge, edges)
        source = _chunk_source(document, chunk, "edge", edge.edge_id)
        sources.append(source)
        source_ids_by_target[("edge", edge.edge_id)].append(source.source_id)


def _edge_from_extracted(
    extracted: _ExtractedEdge, local_nodes: dict[str, GraphNode]
) -> GraphEdge | None:
    """将 LLM 输出的边映射到本 chunk 的节点；端点任一缺失则返回 ``None``。"""
    source = local_nodes.get(_normalize_name(extracted.source_entity))
    target = local_nodes.get(_normalize_name(extracted.target_entity))
    if source is None or target is None:
        return None
    return GraphEdge(
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


def _validate_edge(
    edge: GraphEdge, plugin: RagPlugin, nodes: dict[str, GraphNode]
) -> ValueError | None:
    """校验边的本体合规性；合规返回 ``None``，否则返回异常对象供调用方决策。"""
    try:
        plugin.ontology.validate_edge(edge, nodes)
    except ValueError as error:
        return error
    return None


def _merge_edge(edge: GraphEdge, edges: dict[str, GraphEdge]) -> None:
    """将边合并入全局字典：首次出现直接插入；已存在则合并描述与关键词。"""
    previous = edges.get(edge.edge_id)
    if previous is None:
        edges[edge.edge_id] = edge
        return
    edges[edge.edge_id] = edge.model_copy(
        update={
            "description": _bounded_description(previous.description, edge.description),
            # 保留去重顺序且不超限，前者关键词优先
            "keywords": list(
                dict.fromkeys([*previous.keywords, *edge.keywords])
            )[:_MAX_KEYWORDS_PER_EDGE],
            "extra_meta": {**previous.extra_meta, **edge.extra_meta},
        }
    )


def _chunk_source(
    document: Document, chunk: DocChunk, target_type: str, target_id: str
) -> GraphChunkSource:
    """构造 chunk 与图谱实体之间的溯源记录。"""
    source_id = graph_chunk_source_id(
        target_type=target_type, target_id=target_id, chunk_id=chunk.chunk_id
    )
    return GraphChunkSource(
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        section_id=chunk.section_id,
        chunk_id=chunk.chunk_id,
    )
