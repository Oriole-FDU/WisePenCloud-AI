"""三种图谱检索用例；种子授权和一跳证据准备在通用 Pipeline 排序前完成。"""

from dataclasses import dataclass
from typing import Literal

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import (
    Candidate,
    Fusion,
    MmrDiversity,
    Reranker,
    RetrievalPipeline,
)

from rag.application.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema.filters import MetadataFilters
from rag.retrievers.graph import GraphMatch, GraphRetrievalMode, GraphRetriever
from rag.storage.graph import (
    GraphDirection,
    GraphReader,
    GraphVectorKind,
    graph_fact_sha256,
    graph_vector_record_id,
)
from rag.storage.vector import VectorReader

from .evidence import (
    EvidenceBuilder,
    GraphEvidence,
    GraphEvidenceLoader,
    GraphHit,
    validate_graph_options,
)


@dataclass(frozen=True, slots=True)
class PreparedGraphEvidence:
    """排序阶段只消费已授权证据；来源向量仅供 MMR，不进入应用结果。"""

    evidence: GraphEvidence
    embedding: list[float] | None


class GraphRetrieval:
    """共享固定依赖，每次调用独立装配召回路线和请求内证据准备。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        graph_reader: GraphReader,
        embedding_client: EmbeddingClient,
        entity_reader: VectorReader | None = None,
        relationship_reader: VectorReader | None = None,
        fusion: Fusion[GraphMatch] | None = None,
        reranker: Reranker[PreparedGraphEvidence] | None = None,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._graph_reader = graph_reader
        self._embedding_client = embedding_client
        self._entity_reader = entity_reader  # 实体向量读取器
        self._relationship_reader = relationship_reader  # 关系向量读取器
        self._fusion = fusion  # 多路融合器
        self._reranker = reranker  # 排序阶段精排器

    async def retrieve(
        self,
        query: str,
        *,
        snapshot: ResourceSnapshot,
        mode: GraphRetrievalMode = GraphRetrievalMode.HYBRID,
        top_k: int = 10,
        focus: str = "",
        diversity: Literal["off", "moderate", "high"] = "off",
        candidate_limit: int = 60,
        entity_top_k: int | None = None,
        relationship_top_k: int | None = None,
        neighbor_direction: GraphDirection | None = None,
        neighbor_relation_types: list[str] | None = None,
        neighbor_limit: int = 100,
        filters: MetadataFilters | None = None,
    ) -> list[GraphHit]:
        """一次召回与证据组排序；授权或过期过滤后不补召回。"""

        # 通用 query/top_k/diversity 检查由 Common Pipeline 在召回前执行。
        if candidate_limit < top_k:
            raise ValueError("candidate_limit must be at least top_k")

        # 归一化并校验邻居扩展参数
        direction = validate_graph_options(
            direction=neighbor_direction,
            relation_types=neighbor_relation_types,
            relation_limit=neighbor_limit,
        )

        # 每次调用独立装配召回器
        retriever = GraphRetriever.from_storage(
            embedding_client=self._embedding_client,
            entity_reader=self._entity_reader,
            relationship_reader=self._relationship_reader,
            mode=mode,
            entity_top_k=entity_top_k,
            relationship_top_k=relationship_top_k,
            candidate_limit=candidate_limit,
            filters=filters,
            include_embedding=diversity != "off",  # 需要 MMR 时才回传向量
            fusion=self._fusion,
        )

        async def prepare(
            candidates: list[Candidate[GraphMatch]],
        ) -> list[Candidate[PreparedGraphEvidence]]:
            # 证据准备：核验身份、合并来源、读取一跳邻域
            return await self._prepare(
                candidates,
                snapshot=snapshot,
                direction=direction,
                relation_types=neighbor_relation_types,
                relation_limit=neighbor_limit,
                filters=filters,
            )

        pipeline = RetrievalPipeline[GraphMatch, PreparedGraphEvidence](
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=self._reranker,
            diversity=MmrDiversity(vector_getter=_stored_embedding),
        )
        ranked = await pipeline.run(
            query=query, top_k=top_k, focus=focus, diversity=diversity
        )

        # 只把已授权的证据与分数暴露给调用方
        return [
            GraphHit(item.payload.evidence, item.rank, item.score) for item in ranked
        ]

    async def _prepare(
        self,
        candidates: list[Candidate[GraphMatch]],
        *,
        snapshot: ResourceSnapshot,
        direction: GraphDirection,
        relation_types: list[str] | None,
        relation_limit: int,
        filters: MetadataFilters | None,
    ) -> list[Candidate[PreparedGraphEvidence]]:
        """核对向量身份与当前索引文本后，按逻辑种子合并来源、读取拓扑。"""

        # 先按 record_id 与必要 metadata 过滤，剔除身份不完整的候选
        located = []
        for item in candidates:
            metadata = item.payload.vector.metadata
            if all(
                isinstance(metadata.get(key), str) and metadata[key].strip()
                for key in (
                    "chunk_id",
                    "resource_id",
                    "content_revision",
                    "fact_id",
                    "fact_sha256",
                )
            ) and item.payload.vector.record_id == graph_vector_record_id(
                item.payload.kind, metadata["chunk_id"], metadata["fact_id"]
            ):
                located.append(item)
        if not located:
            return []

        # 共享一次来源读取；证据组按逻辑种子隔离
        loader = GraphEvidenceLoader(
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
            filters=filters,
        )
        await loader.load_chunks(
            [item.payload.vector.metadata["chunk_id"] for item in located]
        )

        groups: dict[tuple[GraphVectorKind, str], EvidenceBuilder] = {}
        first_candidates = {}  # 每个种子取首个候选，用于回传向量与分数
        for item in located:
            match = item.payload.vector
            metadata = match.metadata
            chunk = loader.chunks.get(metadata["chunk_id"])

            # 来源版本必须与当前 Chunk 一致
            if (
                chunk is None
                or chunk.resource_id != metadata["resource_id"]
                or chunk.content_revision != metadata["content_revision"]
            ):
                continue

            nodes, edges = loader.facts[chunk.chunk_id]
            kind, fact_id = item.payload.kind, metadata["fact_id"]
            fact = (nodes if kind is GraphVectorKind.ENTITY else edges).get(fact_id)

            # 事实必须存在，且文本摘要与图版本与当前 Mongo 事实一致
            if (
                fact is None
                or graph_fact_sha256(fact, nodes) != metadata["fact_sha256"]
                or metadata.get("graph_version") != chunk.metadata.get("graph_version")
            ):
                continue

            key = (kind, fact_id)
            group = groups.setdefault(key, EvidenceBuilder(kind, fact_id))
            first_candidates.setdefault(key, item)
            if kind is GraphVectorKind.ENTITY:
                group.add_node(fact, chunk)
            else:
                group.add_edge(fact, nodes, chunk)

        # 只有向量授权确认后的实体能读取一跳；拓扑缺失不能否定合法 Mongo 种子。
        await loader.read_seeds(groups)
        await loader.expand_entities(
            groups,
            direction=direction,
            relation_types=relation_types,
            relation_limit=relation_limit,
        )

        # 组装成排序阶段的候选，顺序即种子发现顺序
        return [
            Candidate(
                candidate_id=f"{kind}:{fact_id}",
                payload=PreparedGraphEvidence(
                    group.build(),
                    first_candidates[(kind, fact_id)].payload.vector.embedding,
                ),
                rank=rank,
                score=first_candidates[(kind, fact_id)].score,
            )
            for rank, ((kind, fact_id), group) in enumerate(groups.items(), start=1)
        ]


def _stored_embedding(payload: PreparedGraphEvidence) -> list[float]:
    """MMR 需要来源向量；调用方未请求时直接报错，避免静默退化。"""
    if payload.embedding is None:
        raise RuntimeError("missing requested graph match embedding")
    return payload.embedding
