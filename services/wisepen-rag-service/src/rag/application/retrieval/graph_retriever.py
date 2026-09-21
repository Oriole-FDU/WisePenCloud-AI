"""图谱候选召回、来源回查、局部精排和最终返回。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from common.utils.ranking import (
    RankCandidate,
    RankDecision,
    RankingPipeline,
    RankQuery,
    RankRequest,
)

from rag.application.document.models import DocChunk
from rag.application.plugins.core.registry import RagPluginRegistry
from rag.application.retrieval.models import (
    GraphRetrieveHit,
    GraphRetrieveLevel,
    GraphRetrieveRequest,
    GraphRetrieveResult,
)
from rag.domain.acl import PermissionScope
from rag.domain.repositories.acl import ResourceAclRepository
from rag.domain.repositories.doc_chunks import DocChunkRepository
from rag.domain.repositories.documents import DocumentRepository
from rag.domain.repositories.graph_edge_vectors import GraphEdgeVectorRepository
from rag.domain.repositories.graph_fact import GraphFactRepository
from rag.domain.repositories.graph_node_vectors import (
    GraphNodeVectorRepository,
    GraphVectorCandidate,
)
from rag.domain.repositories.graph_topology import (
    GraphSourceProjection,
    GraphTopologyRepository,
)
from rag.domain.repositories.index_state import ResourceIndexStateRepository
from rag.domain.repositories.metadata_filters import MetadataFilterCondition
from rag.utils import EmbeddingClient

# --- 内部数据类 ---

@dataclass(frozen=True, slots=True)
class _Candidate:
    candidate_id: str
    kind: Literal["chunk", "fact"]
    text: str
    rank_text: str
    source: GraphSourceProjection
    chunk: DocChunk | None


# --- 图谱检索器 ---

class GraphRetriever:
    """图谱检索独立于构建和发布，只消费已存在的外部投影与 Mongo 权威事实。"""

    def __init__(
        self,
        *,
        enabled: bool,
        topology: GraphTopologyRepository | None,
        node_vectors: GraphNodeVectorRepository,
        edge_vectors: GraphEdgeVectorRepository,
        graph_facts: GraphFactRepository,
        doc_chunks: DocChunkRepository,
        documents: DocumentRepository,
        index_states: ResourceIndexStateRepository,
        resource_acls: ResourceAclRepository,
        ranking_pipeline: RankingPipeline,
        plugin_registry: RagPluginRegistry,
        embedding_client: EmbeddingClient,
        embedding_model: str,
        embedding_dimensions: int,
        embedding_semaphore: asyncio.Semaphore,
    ) -> None:
        self._enabled = enabled
        self._topology = topology
        self._node_vectors = node_vectors
        self._edge_vectors = edge_vectors
        self._graph_facts = graph_facts
        self._doc_chunks = doc_chunks
        self._documents = documents
        self._index_states = index_states
        self._resource_acls = resource_acls
        self._ranking_pipeline = ranking_pipeline
        self._plugin_registry = plugin_registry
        self._embedding_client = embedding_client
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._embedding_semaphore = embedding_semaphore

    async def retrieve(
        self,
        request: GraphRetrieveRequest,
        scope: PermissionScope,
    ) -> GraphRetrieveResult:
        """执行有限图检索；任一来源失效只丢弃该来源，不泄露其资源状态。"""
        query = request.query.strip() if request.query else ""
        if not query and not request.seed_node_ids:
            raise ValueError("query or seed_node_ids must be provided")
        if not self._enabled:
            return GraphRetrieveResult([])
        if self._topology is None:
            raise RuntimeError("graph topology repository is not configured")

        if (
            request.vector_top_n <= 0
            or request.candidate_limit <= 0
            or request.top_k <= 0
        ):
            raise ValueError("graph search candidate counts must be positive")

        # 编译插件过滤器
        metadata_filters = _compile_filters(request, self._plugin_registry)

        # 向量召回（若没有 seed 则进行）
        vector_candidates = await self._retrieve_vectors(
            request,
            query=query,
            scope=scope,
            metadata_filters=metadata_filters,
        )
        if not vector_candidates and not request.seed_node_ids:
            return GraphRetrieveResult([])

        # 图遍历，获取来源投影
        # 各分支先各取 vector_top_n，合并后由 candidate_limit 控制图遍历
        # 和精排的总工作量；两者分别限制不同阶段，不能互相替代。
        traversal_limit = request.candidate_limit
        sources = await self._topology.traverse(
            candidates=vector_candidates,
            seed_node_ids=request.seed_node_ids,
            scope=scope,
            resource_ids=request.resource_ids,
            relation_types=request.relation_types,
            node_categories=request.node_categories,
            direction=request.direction,
            max_depth=request.max_depth,
            metadata_filters=metadata_filters,
            limit=traversal_limit,
        )

        # 加载候选（chunk 或确定性事实）并做可见性过滤
        visible_sources = await self._visible_sources(sources, scope=scope)
        candidates = await self._load_candidates(visible_sources)
        if not candidates:
            return GraphRetrieveResult([])

        # 粗排顺序沿用 LightRAG：向量命中保留分支顺序，遍历结果按
        # 有界的 hop 批次追加。不同 Dense/BM25 分数不合并；candidate_limit
        # 只负责截断候选数量，不负责定义候选顺序。
        candidates = candidates[: request.candidate_limit]
        if not query:
            return GraphRetrieveResult(
                [
                    _to_hit(candidate, score=None)
                    for candidate in candidates[: request.top_k]
                ]
            )
        ranked = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=query),
                candidates=[
                    RankCandidate(
                        candidate_id=item.candidate_id,
                        text=item.rank_text,
                        prior_rank=index,
                    )
                    for index, item in enumerate(candidates, start=1)
                ],
                top_k=request.top_k,
                candidate_limit=len(candidates),
            )
        )
        decision = ranked.decision or RankDecision.IRRELEVANT
        if decision is RankDecision.IRRELEVANT:
            return GraphRetrieveResult([], relevance_decision=decision.value)

        # 请求候选确定后建立的 ACL/active 快照。上游 ACL 通过异步
        # 投影传播，查询中再次读取本地副本既不能消除传播延迟，也会增加 IO。
        by_id = {item.candidate_id: item for item in candidates}

        hits: list[GraphRetrieveHit] = []
        for item in ranked.ranked:
            candidate = by_id.get(item.candidate_id)
            if candidate is None:
                continue
            if candidate.kind == "chunk":
                chunk = candidate.chunk
                if chunk is None:
                    continue
                hits.append(
                    GraphRetrieveHit(
                        resource_id=chunk.resource_id,
                        text=candidate.text,
                        score=item.score,
                        section_id=chunk.section_id,
                        section_path=list(chunk.section_path),
                    )
                )
            else:
                hits.append(
                    GraphRetrieveHit(
                        resource_id=candidate.source.resource_id,
                        text=candidate.text,
                        score=item.score,
                    )
                )
        return GraphRetrieveResult(hits, relevance_decision=decision.value)

    async def _retrieve_vectors(
        self,
        request: GraphRetrieveRequest,
        *,
        query: str,
        scope: PermissionScope,
        metadata_filters: tuple[MetadataFilterCondition, ...],
    ) -> list[GraphVectorCandidate]:
        """根据请求级别执行节点/边向量召回。"""
        if request.seed_node_ids:
            # seed 是调用方已经选定的图入口，不能被向量召回替换或混入
            return []

        async with self._embedding_semaphore:
            query_vector = (
                await self._embedding_client.embed(
                    model=self._embedding_model,
                    texts=[query],
                    dimensions=self._embedding_dimensions,
                )
            )[0]

        tasks = []
        if request.level in (GraphRetrieveLevel.LOW, GraphRetrieveLevel.HYBRID):
            tasks.append(
                self._node_vectors.search_dense(
                    query_vector=query_vector,
                    scope=scope,
                    resource_ids=request.resource_ids,
                    node_categories=request.node_categories,
                    metadata_filters=metadata_filters,
                    limit=request.vector_top_n,
                )
            )
        if request.level in (GraphRetrieveLevel.HIGH, GraphRetrieveLevel.HYBRID):
            tasks.extend(
                (
                    self._edge_vectors.search_dense(
                        query_vector=query_vector,
                        scope=scope,
                        resource_ids=request.resource_ids,
                        relation_types=request.relation_types,
                        metadata_filters=metadata_filters,
                        limit=request.vector_top_n,
                    ),
                    self._edge_vectors.search_bm25(
                        query=query,
                        scope=scope,
                        resource_ids=request.resource_ids,
                        relation_types=request.relation_types,
                        metadata_filters=metadata_filters,
                        limit=request.vector_top_n,
                    ),
                )
            )
        groups = await asyncio.gather(*tasks)
        if request.level is GraphRetrieveLevel.LOW:
            return _merge_vector_candidates([groups[0]])
        if request.level is GraphRetrieveLevel.HIGH:
            return _merge_vector_candidates([groups[0], groups[1]])
        high = _merge_vector_candidates([groups[1], groups[2]])
        return _merge_vector_candidates([groups[0], high])

    async def _load_candidates(
        self,
        sources: list[GraphSourceProjection],
    ) -> list[_Candidate]:
        """将来源投影转换为可精排的候选（chunk 文本或事实文本）。"""
        # 先批量加载所有 LLM 来源的 Chunk 记录；来源契约不再包含字符偏移。
        llm_sources = [source for source in sources if source.source_ids]
        source_records = await self._graph_facts.get_sources(
            [
                source_id
                for source in llm_sources
                for source_id in source.source_ids
            ]
        )
        source_by_id = {source.source_id: source for source in source_records}
        chunks = await self._doc_chunks.get_chunks_by_ids(
            [source.chunk_id for source in source_records]
        )
        allowed_revisions = {
            (source.resource_id, source.content_revision) for source in sources
        }
        documents = await self._documents.get_revisions(list(allowed_revisions))
        chunks_by_id = {
            chunk.chunk_id: chunk
            for chunk in chunks
            if (chunk.resource_id, chunk.content_revision) in allowed_revisions
            and (document := documents.get((chunk.resource_id, chunk.content_revision)))
            is not None
            and chunk.is_valid_for(document)
        }

        # 按拓扑返回顺序聚合候选。一个 Chunk 命中多个图元时只保留一次，
        # 因为模型结果只消费可读正文和定位信息，不消费内部图元指针。
        by_chunk: dict[str, _Candidate] = {}
        ordered: list[_Candidate] = []
        for source in sources:
            if source.source_ids:
                for source_id in source.source_ids:
                    source_record = source_by_id.get(source_id)
                    if source_record is None:
                        continue
                    chunk = chunks_by_id.get(source_record.chunk_id)
                    if (
                        chunk is None
                        or chunk.resource_id != source_record.resource_id
                        or chunk.content_revision != source_record.content_revision
                        or source_record.target_id != source.target_id
                        or source_record.target_type != source.target_type
                    ):
                        continue
                    current = by_chunk.get(chunk.chunk_id)
                    if current is None:
                        candidate = _Candidate(
                            candidate_id=f"chunk:{chunk.chunk_id}",
                            kind="chunk",
                            text=chunk.get_full_text(),
                            rank_text=f"{source.get_fact_text()}\n\n{chunk.get_full_text()}",
                            source=source,
                            chunk=chunk,
                        )
                        by_chunk[chunk.chunk_id] = candidate
                        ordered.append(candidate)
                continue
            if source.producer_id:
                text = source.get_fact_text()
                if text:
                    ordered.append(
                        _Candidate(
                            candidate_id=f"fact:{source.projection_id}",
                            kind="fact",
                            text=text,
                            rank_text=text,
                            source=source,
                            chunk=None,
                        )
                    )
        return ordered

    async def _visible_sources(
        self,
        sources: list[GraphSourceProjection],
        *,
        scope: PermissionScope,
    ) -> list[GraphSourceProjection]:
        """过滤出已发布且用户可读的来源投影。"""
        resource_ids = list(dict.fromkeys(source.resource_id for source in sources))
        states, acls = await asyncio.gather(
            self._index_states.get_states(resource_ids),
            self._resource_acls.get_resource_acls(resource_ids),
        )
        return [
            source
            for source in sources
            if (state := states.get(source.resource_id)) is not None
            and state.applied_content_revision == source.content_revision
            and (acl := acls.get(source.resource_id)) is not None
            and acl.can_read(scope)
        ]

# --- 模块级辅助函数 ---

def _compile_filters(
    request: GraphRetrieveRequest,
    plugin_registry: RagPluginRegistry,
) -> tuple[MetadataFilterCondition, ...]:
    """根据请求中的 plugin_id 和 metadata_filter 编译过滤条件。"""
    if request.metadata_filter is not None and request.plugin_id is None:
        raise ValueError("metadata_filter requires plugin_id")
    if request.plugin_id is None:
        return ()
    plugin = plugin_registry.get(request.plugin_id)
    if plugin is None:
        raise ValueError("RAG plugin is not registered")
    return plugin.compile_filter(request.metadata_filter)


def _to_hit(candidate: _Candidate, *, score: float | None) -> GraphRetrieveHit:
    if candidate.kind == "chunk" and candidate.chunk is not None:
        return GraphRetrieveHit(
            resource_id=candidate.chunk.resource_id,
            text=candidate.text,
            score=score,
            section_id=candidate.chunk.section_id,
            section_path=list(candidate.chunk.section_path),
        )
    return GraphRetrieveHit(resource_id=candidate.source.resource_id, text=candidate.text, score=score)


def _merge_vector_candidates(
    groups: list[list[GraphVectorCandidate]],
) -> list[GraphVectorCandidate]:
    """按 LightRAG 的交替顺序合并分支，并集去重但不融合分数。

    Qdrant 已按相似度返回每个分支。交替取各分支的下一项可以保留
    local/global 两条证据链的覆盖面；重复图元保留第一次出现的位置。
    """
    merged: list[GraphVectorCandidate] = []
    seen: set[tuple[str, str]] = set()
    index = 0
    while True:
        added = False
        for group in groups:
            if index >= len(group):
                continue
            candidate = group[index]
            key = (candidate.target_type, candidate.target_id)
            if key not in seen:
                seen.add(key)
                merged.append(candidate)
            added = True
        if not added:
            return merged
        index += 1
