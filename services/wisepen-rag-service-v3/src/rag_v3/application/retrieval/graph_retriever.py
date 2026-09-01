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

from rag_v3.domain.acl import PermissionScope
from rag_v3.domain.graph import (
    ChunkGraphHit,
    DeterministicGraphFactHit,
    GraphFilterCondition,
    GraphSearchHit,
    GraphSearchLevel,
    GraphSearchRequest,
    GraphSearchResult,
    GraphSourceProjection,
    GraphVectorCandidate,
)
from rag_v3.domain.models import DocChunk, Document
from rag_v3.domain.plugins import GraphPlugin
from rag_v3.domain.repositories.acl import ResourceAclRepository
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.graph import GraphFactRepository
from rag_v3.domain.repositories.graph_projections import (
    GraphEdgeVectorRepository,
    GraphNodeVectorRepository,
    GraphTopologyRepository,
)
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository


@dataclass(frozen=True, slots=True)
class _Candidate:
    candidate_id: str
    kind: Literal["chunk", "fact"]
    text: str
    source: GraphSourceProjection
    chunk: DocChunk | None
    graph_ids: tuple[str, ...]


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
            return GraphSearchResult(())
        if self._topology is None:
            raise RuntimeError("graph topology repository is not configured")
        metadata_filters = _compile_filters(request, self._plugins)
        vector_candidates = await self._retrieve_vectors(
            request,
            scope=scope,
            metadata_filters=metadata_filters,
        )
        if not vector_candidates and not request.seed_node_ids:
            return GraphSearchResult(())
        sources = await self._topology.traverse(
            candidates=vector_candidates,
            seed_node_ids=request.seed_node_ids,
            scope=scope,
            resource_ids=request.resource_ids,
            relation_types=request.relation_types,
            direction=request.direction,
            max_depth=request.max_depth,
            metadata_filters=metadata_filters,
            limit=request.rerank_candidate_n,
        )
        candidates = await self._load_candidates(sources, scope=scope)
        if not candidates:
            return GraphSearchResult(())
        candidates = sorted(
            candidates,
            key=lambda item: (
                item.source.graph_rank,
                item.source.hop_count,
                item.source.target_id,
            ),
        )[: request.rerank_candidate_n]
        ranked = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(
                    semantic_query=request.query,
                    lexical_query=request.query,
                ),
                candidates=tuple(
                    RankCandidate(
                        candidate_id=item.candidate_id,
                        text=item.text,
                        prior_rank=index,
                    )
                    for index, item in enumerate(candidates, start=1)
                ),
                top_k=request.top_k,
                candidate_limit=len(candidates),
            )
        )
        decision = ranked.decision or RankDecision.IRRELEVANT
        if decision is RankDecision.IRRELEVANT:
            return GraphSearchResult((), relevance_decision=decision.value)
        by_id = {item.candidate_id: item for item in candidates}
        # 遍历、Mongo 回读和 rerank 之间可能发生 revision 或 ACL 切换；返回前统一复核。
        final_sources = {
            source.projection_id
            for source in await self._visible_sources(
                [item.source for item in candidates], scope=scope
            )
        }
        hits: list[GraphSearchHit] = []
        for item in ranked.ranked:
            candidate = by_id.get(item.candidate_id)
            if candidate is None or candidate.source.projection_id not in final_sources:
                continue
            if candidate.kind == "chunk":
                chunk = candidate.chunk
                if chunk is None:  # pragma: no cover - 由 _load_candidates 保证
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
        return GraphSearchResult(tuple(hits), relevance_decision=decision.value)

    async def _retrieve_vectors(
        self,
        request: GraphSearchRequest,
        *,
        scope: PermissionScope,
        metadata_filters: tuple[GraphFilterCondition, ...],
    ) -> list[GraphVectorCandidate]:
        if request.seed_node_ids:
            # seed 是调用方已经选定的图入口，不能被向量召回替换或混入。
            return []
        query_vector = await _embed_query(
            self._openai_client,
            model=self._embedding_model,
            dimensions=self._embedding_dimensions,
            query=request.query,
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
                        query=request.query,
                        scope=scope,
                        resource_ids=request.resource_ids,
                        relation_types=request.relation_types,
                        metadata_filters=metadata_filters,
                        limit=request.vector_top_n,
                    ),
                )
            )
        groups = await asyncio.gather(*tasks)
        return _union_vector_candidates(groups)

    async def _load_candidates(
        self,
        sources: list[GraphSourceProjection],
        *,
        scope: PermissionScope,
    ) -> list[_Candidate]:
        visible = await self._visible_sources(sources, scope=scope)
        llm_sources = [source for source in visible if source.evidence_ids]
        deterministic = [source for source in visible if source.producer_id]
        evidences = await self._graph_facts.get_evidences(
            [evidence_id for source in llm_sources for evidence_id in source.evidence_ids]
        )
        evidence_by_id = {evidence.evidence_id: evidence for evidence in evidences}
        chunks = await self._doc_chunks.get_chunks_by_ids(
            [evidence.chunk_id for evidence in evidences]
        )
        visible_chunks, documents = await self._visible_chunks(chunks, scope=scope)
        chunks_by_id = {chunk.chunk_id: chunk for chunk in visible_chunks}

        by_chunk: dict[str, _Candidate] = {}
        for source in llm_sources:
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
                graph_ids = tuple(
                    dict.fromkeys((*(current.graph_ids if current else ()), source.target_id))
                )
                candidate = _Candidate(
                    candidate_id=f"chunk:{chunk.chunk_id}",
                    kind="chunk",
                    text=_chunk_rerank_text(chunk),
                    source=(current.source if current is not None else source),
                    chunk=chunk,
                    graph_ids=graph_ids,
                )
                by_chunk[chunk.chunk_id] = candidate

        facts: list[_Candidate] = []
        for source in deterministic:
            text = _fact_rerank_text(source)
            if not text:
                continue
            facts.append(
                _Candidate(
                    candidate_id=f"fact:{source.projection_id}",
                    kind="fact",
                    text=text,
                    source=source,
                    chunk=None,
                    graph_ids=(source.target_id,),
                )
            )
        return [*by_chunk.values(), *facts]

    async def _visible_sources(
        self,
        sources: list[GraphSourceProjection],
        *,
        scope: PermissionScope,
    ) -> list[GraphSourceProjection]:
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

    async def _visible_chunks(
        self,
        chunks: list[DocChunk],
        *,
        scope: PermissionScope,
    ) -> tuple[list[DocChunk], dict[tuple[str, str], Document]]:
        resource_ids = list(dict.fromkeys(chunk.resource_id for chunk in chunks))
        states, acls = await asyncio.gather(
            self._index_states.get_states(resource_ids),
            self._resource_acls.get_resource_acls(resource_ids),
        )
        revisions = [
            (resource_id, state.applied_content_revision)
            for resource_id, state in states.items()
            if state.applied_content_revision is not None
        ]
        documents = await self._documents.get_revisions(revisions)
        visible = [
            chunk
            for chunk in chunks
            if (state := states.get(chunk.resource_id)) is not None
            and state.applied_content_revision == chunk.content_revision
            and (acl := acls.get(chunk.resource_id)) is not None
            and acl.can_read(scope)
            and (chunk.resource_id, chunk.content_revision) in documents
        ]
        return visible, documents


def _compile_filters(
    request: GraphSearchRequest,
    plugins: tuple[GraphPlugin, ...],
) -> tuple[GraphFilterCondition, ...]:
    if request.plugin_id is None:
        return ()
    plugin = next((item for item in plugins if item.plugin_id == request.plugin_id), None)
    if plugin is None:
        raise ValueError("graph plugin is not registered")
    return plugin.compile_filter(request.metadata_filter)


def _union_vector_candidates(
    groups: list[list[GraphVectorCandidate]],
) -> list[GraphVectorCandidate]:
    """图元按逻辑 ID 并集，分支 rank 仅用于后续粗排，不融合相似度。"""
    by_target: dict[tuple[str, str], GraphVectorCandidate] = {}
    for candidate in (item for group in groups for item in group):
        key = (candidate.target_type, candidate.target_id)
        current = by_target.get(key)
        if current is None or (candidate.rank, candidate.branch) < (
            current.rank,
            current.branch,
        ):
            by_target[key] = candidate
    return sorted(
        by_target.values(),
        key=lambda item: (item.rank, item.branch, item.target_id),
    )


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
    # Evidence 可以引用多个相邻 span，但每一段都必须属于它声明的目标 Chunk。
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


def _chunk_rerank_text(chunk: DocChunk) -> str:
    title = " > ".join(chunk.section_path)
    return f"{title}\n\n{chunk.raw_text}" if title else chunk.raw_text


def _fact_rerank_text(source: GraphSourceProjection) -> str:
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
