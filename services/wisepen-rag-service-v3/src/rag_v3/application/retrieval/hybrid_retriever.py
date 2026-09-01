"""两路文档召回、请求级 ACL 快照、精排和三路动态父块构建。"""

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from common.utils.document import SourceSpan
from common.utils.ranking import (
    RankCandidate,
    RankDecision,
    RankingPipeline,
    RankQuery,
    RankRequest,
)
from openai import AsyncOpenAI

from rag_v3.application.document.models import DocChunk, Document
from rag_v3.application.retrieval.models import (
    ChunkHit,
    DynamicParent,
    HybridQuery,
    HybridRetrievalResult,
)
from rag_v3.domain.acl import PermissionScope
from rag_v3.domain.repositories.acl import ResourceAclRepository
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.document_vectors import (
    DocumentVectorRepository,
    VectorCandidate,
)
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository

# --- 常量配置 ---

_CANDIDATE_LIMIT = 30
_SHORT_SECTION_MAX_CHARS = 4_000
_SECTION_RETURN_MAX_CHARS = 8_000
_SECTION_RETURN_COVERAGE = 0.8


# --- 内部辅助数据类 ---

@dataclass(frozen=True, slots=True)
class _RankedChunk:
    chunk: DocChunk
    rank: int
    score: float


# --- 混合检索器 ---

class HybridRetriever:
    """执行传统 RAG 检索，不隐式进入图谱检索或保存父块。"""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        doc_chunks: DocChunkRepository,
        document_vectors: DocumentVectorRepository,
        index_states: ResourceIndexStateRepository,
        resource_acls: ResourceAclRepository,
        ranking_pipeline: RankingPipeline,
        openai_client: AsyncOpenAI,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._document_vectors = document_vectors
        self._index_states = index_states
        self._resource_acls = resource_acls
        self._ranking_pipeline = ranking_pipeline
        self._openai_client = openai_client
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    async def retrieve(
        self,
        query: HybridQuery,
        *,
        scope: PermissionScope,
    ) -> HybridRetrievalResult:
        """独立召回两路 Top 30，在 Mongo 当前事实和 ACL 快照校验后才产生 Hit。"""
        # 输入校验
        semantic_query = query.semantic_query.strip()
        if not semantic_query:
            raise ValueError("semantic_query must not be empty")
        if query.top_k <= 0:
            raise ValueError("top_k must be positive")
        lexical_query = query.lexical_query.strip() or semantic_query

        # 1. 生成查询向量并并行检索稠密和BM25
        query_vector = await _embed_query(
            self._openai_client,
            model=self._embedding_model,
            dimensions=self._embedding_dimensions,
            query=semantic_query,
        )
        dense, lexical = await asyncio.gather(
            self._document_vectors.search_dense(
                query_vector=query_vector,
                scope=scope,
                limit=_CANDIDATE_LIMIT,
            ),
            self._document_vectors.search_bm25(
                query=lexical_query,
                scope=scope,
                limit=_CANDIDATE_LIMIT,
            ),
        )

        # 2. 并集合并候选。这里不对两路 rank 排序；两路结果去重后
        # 全量交给 RankingPipeline，rank 只作为回查审计信号保留。
        candidates = _union_candidates(dense, lexical)
        if not candidates:
            return _empty_result()

        # 3. 加载候选 chunk，并建立本次请求唯一的 active/ACL 快照
        chunks = await self._doc_chunks.get_chunks_by_ids(
            [candidate.chunk_id for candidate in candidates]
        )
        visible_chunks, documents = await self._load_visible_chunks(chunks, scope=scope)
        if not visible_chunks:
            return _empty_result()

        # 4. 准备精排输入
        chunks_by_id = {chunk.chunk_id: chunk for chunk in visible_chunks}
        ranking_candidates = [
            RankCandidate(
                candidate_id=candidate.chunk_id,
                # 仅使用标题+正文，不包含关键词或前缀（防止污染）
                text=chunks_by_id[candidate.chunk_id].get_full_text(),
                prior_rank=index,
            )
            for index, candidate in enumerate(candidates, start=1)
            if candidate.chunk_id in chunks_by_id
        ]

        # 5. 执行精排
        rank_result = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(
                    semantic_query=semantic_query,
                    lexical_query=lexical_query,
                ),
                candidates=ranking_candidates,
                top_k=query.top_k,
                candidate_limit=len(ranking_candidates),
            )
        )
        decision = rank_result.decision or RankDecision.IRRELEVANT
        if decision is RankDecision.IRRELEVANT:
            return _empty_result()

        # 6. 构建命中列表
        ranked_chunks = [
            _RankedChunk(
                chunk=chunks_by_id[item.candidate_id],
                rank=item.rank,
                score=item.score,
            )
            for item in rank_result.ranked
            if item.candidate_id in chunks_by_id
        ]
        hits = [_to_hit(item) for item in ranked_chunks]

        # 7. 构建动态父块
        revision_chunks = await self._doc_chunks.get_revisions_chunks(list(documents))
        parents = _build_dynamic_parents(
            ranked_chunks,
            documents=documents,
            revision_chunks=revision_chunks,
        )

        return HybridRetrievalResult(
            hits=hits,
            parents=parents,
            relevance_decision=decision,
        )

    async def _load_visible_chunks(
        self,
        chunks: Sequence[DocChunk],
        *,
        scope: PermissionScope,
    ) -> tuple[list[DocChunk], Mapping[tuple[str, str], Document]]:
        """过滤掉未发布、无权限或 span 失效的 chunk。"""
        resource_ids = list(dict.fromkeys(chunk.resource_id for chunk in chunks))

        # 并行获取状态和 ACL
        states, resource_acls = await asyncio.gather(
            self._index_states.get_states(resource_ids),
            self._resource_acls.get_resource_acls(resource_ids),
        )

        # 加载活跃 revision 的文档
        active_revisions = [
            (resource_id, state.applied_content_revision)
            for resource_id, state in states.items()
            if state.applied_content_revision is not None
        ]
        documents = await self._documents.get_revisions(active_revisions)

        # 逐 chunk 校验
        visible: list[DocChunk] = []
        for chunk in chunks:
            state = states.get(chunk.resource_id)
            resource_acl = resource_acls.get(chunk.resource_id)
            document = documents.get((chunk.resource_id, chunk.content_revision))
            if (
                state is None
                or state.applied_content_revision != chunk.content_revision
            ):
                continue
            if resource_acl is None or not resource_acl.can_read(scope):
                continue
            if document is None or not chunk.is_valid_for(document):
                continue
            visible.append(chunk)
        return visible, documents


# --- 辅助函数：向量嵌入 ---

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
    if len(response.data) != 1:
        raise ValueError("embedding response count does not match query")
    vector = list(response.data[0].embedding)
    if len(vector) != dimensions:
        raise ValueError("embedding response dimensions do not match settings")
    return vector


# --- 辅助函数：候选合并 ---

def _union_candidates(
    dense: Sequence[VectorCandidate],
    lexical: Sequence[VectorCandidate],
) -> list[VectorCandidate]:
    """按 Chunk ID 取并集，保留两路独立 rank，不产生新的排序分数。"""
    by_chunk_id: dict[str, VectorCandidate] = {}
    for candidate in (*dense, *lexical):
        current = by_chunk_id.get(candidate.chunk_id)
        if current is None:
            by_chunk_id[candidate.chunk_id] = candidate
            continue
        # 若 payload 身份冲突则丢弃该候选（索引不一致）
        if (
            current.resource_id != candidate.resource_id
            or current.content_revision != candidate.content_revision
        ):
            by_chunk_id.pop(candidate.chunk_id)
            continue
        # 保留两路 rank，缺失值用0表示未命中
        by_chunk_id[candidate.chunk_id] = VectorCandidate(
            chunk_id=current.chunk_id,
            resource_id=current.resource_id,
            content_revision=current.content_revision,
            dense_rank=current.dense_rank or candidate.dense_rank,
            lexical_rank=current.lexical_rank or candidate.lexical_rank,
        )

    # 不做 min(rank)、RRF 或其他人为粗排。字典顺序只保证结果稳定，
    # 不表达 Dense/BM25 的跨路优先级。
    return list(by_chunk_id.values())


# --- 辅助函数：结果转换 ---

def _to_hit(item: _RankedChunk) -> ChunkHit:
    return ChunkHit(
        chunk_id=item.chunk.chunk_id,
        resource_id=item.chunk.resource_id,
        content_revision=item.chunk.content_revision,
        section_id=item.chunk.section_id,
        section_path=item.chunk.section_path,
        rerank_score=item.score,
        node_ids=list(dict.fromkeys(item.chunk.extracted_node_ids)),
    )


# --- 辅助函数：动态父块构建 ---

def _build_dynamic_parents(
    ranked_chunks: Sequence[_RankedChunk],
    *,
    documents: Mapping[tuple[str, str], Document],
    revision_chunks: Sequence[DocChunk],
) -> list[DynamicParent]:
    """保留 Chat 的三路选择，但每条路径都返回完整 Markdown 区间。"""
    # 按 revision 分组全部 chunk
    chunks_by_revision: dict[tuple[str, str], list[DocChunk]] = defaultdict(list)
    for chunk in revision_chunks:
        chunks_by_revision[(chunk.resource_id, chunk.content_revision)].append(chunk)

    # 按 (resource_id, content_revision, section_id) 分组候选
    grouped: dict[tuple[str, str, str | None], list[_RankedChunk]] = defaultdict(list)
    for item in ranked_chunks:
        grouped[
            (item.chunk.resource_id, item.chunk.content_revision, item.chunk.section_id)
        ].append(item)

    parents: list[DynamicParent] = []
    for (resource_id, content_revision, section_id), items in grouped.items():
        document = documents[(resource_id, content_revision)]
        section = next(
            (
                item
                for item in document.structure.sections
                if item.section_id == section_id
            ),
            None,
        )
        # 作用范围：section 自身 span 或全文档
        scope = (
            section.own_span
            if section is not None
            else SourceSpan(0, len(document.raw_content))
        )
        score = max(item.score for item in items)
        matched_chunk_ids = [
            item.chunk.chunk_id for item in sorted(items, key=lambda item: item.rank)
        ]

        # 情况1：section 短，直接返回整个 section
        if section is not None and scope.length <= _SHORT_SECTION_MAX_CHARS:
            parents.append(
                _parent_from_span(document, scope, section_id, matched_chunk_ids, score)
            )
            continue

        # 情况2：扩展各组，若覆盖率高且长度允许则返回整个 section
        expanded_groups = _expanded_groups(
            items,
            scope=scope,
            chunks=chunks_by_revision[(resource_id, content_revision)],
        )
        if (
            section is not None
            and scope.length <= _SECTION_RETURN_MAX_CHARS
            and len(expanded_groups) == 1
            and _covered_length([span for span, _ in expanded_groups], scope)
            / scope.length
            >= _SECTION_RETURN_COVERAGE
        ):
            parents.append(
                _parent_from_span(document, scope, section_id, matched_chunk_ids, score)
            )
            continue

        # 情况3：返回多个扩展组
        for span, group in expanded_groups:
            parents.append(
                _parent_from_span(
                    document,
                    span,
                    section_id,
                    [item.chunk.chunk_id for item in group],
                    max(item.score for item in group),
                )
            )
    return sorted(parents, key=lambda item: (-item.score, item.parent_id))


def _expanded_groups(
    items: Sequence[_RankedChunk],
    *,
    scope: SourceSpan,
    chunks: Sequence[DocChunk],
) -> list[tuple[SourceSpan, list[_RankedChunk]]]:
    """将相邻 chunk 聚合并向外扩展一个相邻 chunk。"""
    by_index = {chunk.chunk_index: chunk for chunk in chunks}
    ordered = sorted(items, key=lambda item: item.chunk.chunk_index)
    groups: list[list[_RankedChunk]] = [[ordered[0]]]
    for item in ordered[1:]:
        if _can_join_chunk_group(groups[-1][-1], item):
            groups[-1].append(item)
        else:
            groups.append([item])

    expanded: list[tuple[SourceSpan, list[_RankedChunk]]] = []
    for group in groups:
        first, last = group[0].chunk, group[-1].chunk
        before = _neighbor_chunk(by_index, first, offset=-1)
        after = _neighbor_chunk(by_index, last, offset=1)
        start = (before or first).chunk_span.start_offset
        end = (after or last).chunk_span.end_offset
        expanded.append(
            (
                SourceSpan(max(start, scope.start_offset), min(end, scope.end_offset)),
                group,
            )
        )
    return expanded


def _can_join_chunk_group(previous: _RankedChunk, current: _RankedChunk) -> bool:
    return (
        previous.chunk.section_id == current.chunk.section_id
        and 1 <= current.chunk.chunk_index - previous.chunk.chunk_index <= 2
    )


def _neighbor_chunk(
    chunks_by_index: Mapping[int, DocChunk],
    chunk: DocChunk,
    *,
    offset: int,
) -> DocChunk | None:
    neighbor = chunks_by_index.get(chunk.chunk_index + offset)
    if neighbor is None or neighbor.section_id != chunk.section_id:
        return None
    return neighbor


def _parent_from_span(
    document: Document,
    span: SourceSpan,
    section_id: str | None,
    matched_chunk_ids: list[str],
    score: float,
) -> DynamicParent:
    identity = f"{document.resource_id}\0{document.revision.content_revision}\0{span.start_offset}\0{span.end_offset}"
    return DynamicParent(
        parent_id="rpar_" + sha256(identity.encode("utf-8")).hexdigest()[:24],
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        section_ids=(section_id,) if section_id is not None else (),
        text=document.raw_content[span.start_offset : span.end_offset],
        source_spans=[span],
        matched_chunk_ids=matched_chunk_ids,
        score=score,
    )


def _covered_length(spans: Sequence[SourceSpan], scope: SourceSpan) -> int:
    """合并 spans 并计算在 scope 内的覆盖总长度。"""
    merged: list[SourceSpan] = []
    for span in sorted(spans, key=lambda item: item.start_offset):
        if not merged or span.start_offset > merged[-1].end_offset:
            merged.append(span)
            continue
        previous = merged[-1]
        merged[-1] = SourceSpan(
            previous.start_offset, max(previous.end_offset, span.end_offset)
        )
    return sum(span.length for span in merged)


# --- 辅助函数：空结果 ---

def _empty_result() -> HybridRetrievalResult:
    return HybridRetrievalResult(
        hits=[],
        parents=[],
        relevance_decision=RankDecision.IRRELEVANT,
    )
