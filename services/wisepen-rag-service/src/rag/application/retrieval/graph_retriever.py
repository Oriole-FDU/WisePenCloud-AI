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
from openai import AsyncOpenAI

from rag.application.document.models import DocChunk, Document
from rag.application.graph.models import GraphPlugin
from rag.application.retrieval.models import (
    ChunkGraphHit,
    DeterministicGraphFactHit,
    GraphSearchHit,
    GraphSearchLevel,
    GraphSearchRequest,
    GraphSearchResult,
)
from rag.domain.acl import PermissionScope
from rag.domain.repositories.acl import ResourceAclRepository
from rag.domain.repositories.doc_chunks import DocChunkRepository
from rag.domain.repositories.documents import DocumentRepository
from rag.domain.repositories.graph_edge_vectors import GraphEdgeVectorRepository
from rag.domain.repositories.graph_fact import GraphFactRepository
from rag.domain.repositories.graph_node_vectors import (
    GraphFilterCondition,
    GraphNodeVectorRepository,
    GraphVectorCandidate,
)
from rag.domain.repositories.graph_topology import (
    GraphSourceProjection,
    GraphTopologyRepository,
)
from rag.domain.repositories.index_state import ResourceIndexStateRepository

# --- 内部数据类 ---

@dataclass(frozen=True, slots=True)
class _Candidate:
    candidate_id: str
    kind: Literal["chunk", "fact"]
    text: str
    source: GraphSourceProjection
    chunk: DocChunk | None
    graph_ids: list[str]


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
        plugins: tuple[GraphPlugin, ...],
        openai_client: AsyncOpenAI,
        embedding_model: str,
        embedding_dimensions: int,
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
        self._plugins = plugins
        self._openai_client = openai_client
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    async def search(
        self,
        request: GraphSearchRequest,
        scope: PermissionScope,
    ) -> GraphSearchResult:
        """执行有限图检索；任一来源失效只丢弃该来源，不泄露其资源状态。"""
        if not self._enabled:
            return GraphSearchResult([])
        if self._topology is None:
            raise RuntimeError("graph topology repository is not configured")

        query = request.query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if (
            request.vector_top_n <= 0
            or request.candidate_limit <= 0
            or request.top_k <= 0
        ):
            raise ValueError("graph search candidate counts must be positive")

        # 编译插件过滤器
        metadata_filters = _compile_filters(request, self._plugins)

        # 向量召回（若没有 seed 则进行）
        vector_candidates = await self._retrieve_vectors(
            request,
            query=query,
            scope=scope,
            metadata_filters=metadata_filters,
        )
        if not vector_candidates and not request.seed_node_ids:
            return GraphSearchResult([])

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
            direction=request.direction,
            max_depth=request.max_depth,
            metadata_filters=metadata_filters,
            limit=traversal_limit,
        )

        # 加载候选（chunk 或确定性事实）并做可见性过滤
        visible_sources = await self._visible_sources(sources, scope=scope)
        candidates = await self._load_candidates(visible_sources)
        if not candidates:
            return GraphSearchResult([])

        # 粗排顺序沿用 LightRAG：向量命中保留分支顺序，遍历结果按
        # 有界的 hop 批次追加。不同 Dense/BM25 分数不合并；candidate_limit
        # 只负责截断候选数量，不负责定义候选顺序。
        candidates = candidates[: request.candidate_limit]
        ranked = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(
                    semantic_query=query,
                    lexical_query=query,
                ),
                candidates=[
                    RankCandidate(
                        candidate_id=item.candidate_id,
                        text=item.text,
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
            return GraphSearchResult([], relevance_decision=decision.value)

        # 请求候选确定后建立的 ACL/active 快照。上游 ACL 通过异步
        # 投影传播，查询中再次读取本地副本既不能消除传播延迟，也会增加 IO。
        by_id = {item.candidate_id: item for item in candidates}

        hits: list[GraphSearchHit] = []
        for item in ranked.ranked:
            candidate = by_id.get(item.candidate_id)
            if candidate is None:
                continue
            if candidate.kind == "chunk":
                chunk = candidate.chunk
                if chunk is None:
                    continue
                hits.append(
                    ChunkGraphHit(
                        chunk_id=chunk.chunk_id,
                        resource_id=chunk.resource_id,
                        content_revision=chunk.content_revision,
                        section_id=chunk.section_id,
                        section_path=chunk.section_path,
                        graph_ids=candidate.graph_ids,
                        rerank_score=item.score,
                    )
                )
            else:
                hits.append(
                    DeterministicGraphFactHit(
                        target_type=candidate.source.target_type,
                        target_id=candidate.source.target_id,
                        resource_id=candidate.source.resource_id,
                        content_revision=candidate.source.content_revision,
                        producer_id=candidate.source.producer_id or "",
                        rerank_score=item.score,
                    )
                )
        return GraphSearchResult(hits, relevance_decision=decision.value)

    async def _retrieve_vectors(
        self,
        request: GraphSearchRequest,
        *,
        query: str,
        scope: PermissionScope,
        metadata_filters: tuple[GraphFilterCondition, ...],
    ) -> list[GraphVectorCandidate]:
        """根据请求级别执行节点/边向量召回。"""
        if request.seed_node_ids:
            # seed 是调用方已经选定的图入口，不能被向量召回替换或混入
            return []

        query_vector = await _embed_query(
            self._openai_client,
            model=self._embedding_model,
            dimensions=self._embedding_dimensions,
            query=query,
        )

        tasks = []
        if request.level in (GraphSearchLevel.LOW, GraphSearchLevel.HYBRID):
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
        if request.level in (GraphSearchLevel.HIGH, GraphSearchLevel.HYBRID):
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
        if request.level is GraphSearchLevel.LOW:
            return _merge_vector_candidates([groups[0]])
        if request.level is GraphSearchLevel.HIGH:
            return _merge_vector_candidates([groups[0], groups[1]])
        high = _merge_vector_candidates([groups[1], groups[2]])
        return _merge_vector_candidates([groups[0], high])

    async def _load_candidates(
        self,
        sources: list[GraphSourceProjection],
    ) -> list[_Candidate]:
        """将来源投影转换为可精排的候选（chunk 文本或事实文本）。"""
        # 先批量加载所有 LLM 来源需要的 Evidence；候选仍按 topology 的
        # 顺序逐项组装，避免把 Chunk 和确定性事实拆成两段而改变粗排顺序。
        llm_sources = [source for source in sources if source.evidence_ids]

        # 加载 evidence 和对应的 chunk
        evidences = await self._graph_facts.get_evidences(
            [
                evidence_id
                for source in llm_sources
                for evidence_id in source.evidence_ids
            ]
        )
        evidence_by_id = {evidence.evidence_id: evidence for evidence in evidences}
        chunks = await self._doc_chunks.get_chunks_by_ids(
            [evidence.chunk_id for evidence in evidences]
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

        # 按拓扑返回顺序聚合候选（一个 chunk 可能对应多个图元）。首次
        # 出现位置就是粗排位置，后续同 Chunk 命中只补充 graph_ids。
        by_chunk: dict[str, _Candidate] = {}
        ordered: list[_Candidate] = []
        for source in sources:
            if source.evidence_ids:
                for evidence_id in source.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is None:
                        continue
                    chunk = chunks_by_id.get(evidence.chunk_id)
                    if (
                        chunk is None
                        or chunk.resource_id != evidence.resource_id
                        or not _valid_evidence(evidence, source, chunk, documents)
                    ):
                        continue
                    current = by_chunk.get(chunk.chunk_id)
                    if current is None:
                        candidate = _Candidate(
                            candidate_id=f"chunk:{chunk.chunk_id}",
                            kind="chunk",
                            text=chunk.get_full_text(),
                            source=source,
                            chunk=chunk,
                            graph_ids=[source.target_id],
                        )
                        by_chunk[chunk.chunk_id] = candidate
                        ordered.append(candidate)
                    elif source.target_id not in current.graph_ids:
                        current.graph_ids.append(source.target_id)
                continue
            if source.producer_id:
                text = _fact_rerank_text(source)
                if text:
                    ordered.append(
                        _Candidate(
                            candidate_id=f"fact:{source.projection_id}",
                            kind="fact",
                            text=text,
                            source=source,
                            chunk=None,
                            graph_ids=[source.target_id],
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
    request: GraphSearchRequest,
    plugins: tuple[GraphPlugin, ...],
) -> tuple[GraphFilterCondition, ...]:
    """根据请求中的 plugin_id 和 metadata_filter 编译过滤条件。"""
    if request.metadata_filter is not None and request.plugin_id is None:
        raise ValueError("metadata_filter requires plugin_id")
    if request.plugin_id is None:
        return ()
    plugin = next(
        (item for item in plugins if item.plugin_id == request.plugin_id), None
    )
    if plugin is None:
        raise ValueError("graph plugin is not registered")
    return plugin.compile_filter(request.metadata_filter)


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


async def _embed_query(
    openai_client: AsyncOpenAI,
    *,
    model: str,
    dimensions: int,
    query: str,
) -> list[float]:
    response = await openai_client.embeddings.create(
        model=model,
        input=query,
        dimensions=dimensions,
    )
    if len(response.data) != 1 or len(response.data[0].embedding) != dimensions:
        raise ValueError("embedding response dimensions do not match settings")
    return list(response.data[0].embedding)


def _valid_evidence(
    evidence,
    source: GraphSourceProjection,
    chunk: DocChunk,
    documents: dict[tuple[str, str], Document],
) -> bool:
    """校验 evidence 与来源投影及 chunk 的一致性和 span 归属。"""
    if (
        evidence.resource_id != source.resource_id
        or evidence.content_revision != source.content_revision
        or evidence.target_id != source.target_id
        or evidence.target_type != source.target_type
    ):
        return False
    document = documents.get((evidence.resource_id, evidence.content_revision))
    if document is None:
        return False
    # Evidence 可以引用多个相邻 span，但每一段都必须属于它声明的目标 Chunk
    if not all(
        any(
            chunk_span.start_offset <= span.start_offset
            and span.end_offset <= chunk_span.end_offset
            for chunk_span in chunk.source_spans
        )
        for span in evidence.source_spans
    ):
        return False
    quote = "".join(
        document.raw_content[span.start_offset : span.end_offset]
        for span in evidence.source_spans
    )
    return quote == evidence.quote_text


def _fact_rerank_text(source: GraphSourceProjection) -> str:
    """为确定性事实生成用于精排的文本。"""
    if source.edge is not None:
        return " ".join(
            part
            for part in (
                source.source_node_name or source.edge.source_node_id,
                source.edge.relation_type,
                source.target_node_name or source.edge.target_node_id,
                source.edge.description,
            )
            if part
        )
    if source.node is not None:
        return " ".join(
            part for part in (source.node.name, source.node.description) if part
        )
    return ""
