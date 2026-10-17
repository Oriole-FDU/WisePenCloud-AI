"""普通向量检索用例；授权回查完成后，正文才能进入重排与结果组装。"""

from dataclasses import dataclass, field, replace
from typing import Literal

from common.logger import warning
from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import (
    Candidate,
    Fusion,
    MmrDiversity,
    Reranker,
    RetrievalPipeline,
)

from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema import Chunk, GraphRelationOption, parse_graph_facts
from rag.core.schema.filters import MetadataFilters
from rag.core.schema.graph import GraphFactValidationError
from rag.retrievers.vector import VectorRetrievalMode, VectorRetriever
from rag.storage.graph import GraphReader
from rag.storage.vector import VectorMatch, VectorReader

# ChunkHit 响应中排除的内部字段；不修改存储中的 metadata。
_CHUNK_HIT_EXCLUDED_METADATA_KEYS = {"entities", "relationships"}


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """排序阶段的运行时 payload；chunk 已通过资源授权、版本及投影身份检查。"""

    chunk: Chunk
    embedding: list[float] | None  # 仅用于多样化，不进入应用结果。


@dataclass(frozen=True, slots=True)
class GraphNodeReference:
    """命中 Chunk 中可供调用方选择的实体；来源身份沿用外层 ChunkHit。"""

    id: str  # GraphNode.id：本体、类别与规范化名称联合确定。
    relations: list[GraphRelationOption] | None = None  # None 未查询；[] 无可见连接。


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """应用可见的正文命中；score 表示最终排序阶段，不限定为重排分数。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    text: str  # Chunk.raw_text；上下文不拼接到正文，业务 metadata 单独返回。
    rank: int
    score: float
    seed_entities: list[GraphNodeReference] = field(default_factory=list)
    metadata: dict[str, object] = field(
        default_factory=dict
    )  # 当前已授权 Chunk 的 metadata，排除模块常量指定的内部字段。


class VectorRetrieval:
    """共享固定依赖；每次调用构造 Retriever/Pipeline，显式使用调用方快照。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        vector_reader: VectorReader,
        embedding_client: EmbeddingClient,
        fusion: Fusion[VectorMatch] | None = None,
        reranker: Reranker[PreparedChunk] | None = None,
        graph_reader: GraphReader | None = None,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._vector_reader = vector_reader
        self._embedding_client = embedding_client
        self._fusion = fusion
        self._reranker = reranker
        self._graph_reader = graph_reader

    async def retrieve(
        self,
        query: str,
        *,
        snapshot: ResourceSnapshot,
        mode: VectorRetrievalMode = VectorRetrievalMode.HYBRID,
        top_k: int = 5,
        focus: str = "",
        diversity: Literal["off", "moderate", "high"] = "off",
        filters: MetadataFilters | None = None,
        dense_top_k: int | None = None,
        sparse_top_k: int | None = None,
        candidate_limit: int = 60,
    ) -> list[ChunkHit]:
        """一次召回、授权准备和排序；授权过滤后不补召回或重新观察发布/ACL。"""
        include_embedding = diversity != "off"
        # 已连接的仓储与客户端共享；本次只组装固定配置的召回组件，不新建连接。
        retriever = VectorRetriever.from_storage(
            reader=self._vector_reader,
            embedding_client=self._embedding_client,
            mode=mode,
            fusion=self._fusion,
            dense_top_k=dense_top_k,
            sparse_top_k=sparse_top_k,
            candidate_limit=candidate_limit,
            filters=filters,
            include_embedding=include_embedding,
        )

        async def prepare(
            candidates: list[Candidate[VectorMatch]],
        ) -> list[Candidate[PreparedChunk]]:
            return await self._prepare(candidates, snapshot=snapshot)

        pipeline = RetrievalPipeline[VectorMatch, PreparedChunk](
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=self._reranker,
            diversity=MmrDiversity(vector_getter=_stored_embedding),
        )
        ranked = await pipeline.run(
            query=query, top_k=top_k, focus=focus, diversity=diversity
        )
        return await build_chunk_hits(
            ranked,
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
        )

    async def _prepare(
        self,
        candidates: list[Candidate[VectorMatch]],
        *,
        snapshot: ResourceSnapshot,
    ) -> list[Candidate[PreparedChunk]]:
        """先筛除无定位投影，再批量授权回查；正文不能反向修补错误 metadata。"""
        located = [
            item
            for item in candidates
            if all(
                isinstance(value, str) and value.strip()
                for value in (
                    item.payload.metadata.get("resource_id"),
                    item.payload.metadata.get("content_revision"),
                )
            )
        ]
        if not located:
            return []
        chunks = await self._snapshot_reader.read_chunks(
            list(dict.fromkeys(item.payload.record_id for item in located)),
            snapshot=snapshot,
        )
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        prepared = []
        for item in located:
            chunk = by_id.get(item.payload.record_id)
            if (
                chunk is None
                or item.payload.metadata["resource_id"] != chunk.resource_id
                or item.payload.metadata["content_revision"] != chunk.content_revision
            ):
                continue
            prepared.append(
                Candidate(
                    candidate_id=item.candidate_id,
                    payload=PreparedChunk(chunk, item.payload.embedding),
                    rank=len(prepared) + 1,
                    score=item.score,
                )
            )
        return prepared


def chunk_seed_entities(chunk: Chunk) -> list[GraphNodeReference]:
    """从已授权正文补充实体候选；图事实损坏不撤销正文命中。"""
    if "entities" not in chunk.metadata and "relationships" not in chunk.metadata:
        return []
    try:
        nodes, _ = parse_graph_facts(chunk)
    except GraphFactValidationError as exc:
        warning("chunk_seed_entities_rejected", chunk_id=chunk.chunk_id, error=str(exc))
        return []
    return [GraphNodeReference(node.id) for node in nodes.values()]


async def build_chunk_hits(
    ranked: list[Candidate[PreparedChunk]],
    *,
    snapshot_reader: ResourceSnapshotReader,
    graph_reader: GraphReader | None,
    snapshot: ResourceSnapshot,
) -> list[ChunkHit]:
    """两类检索共用结果组装；只为最终命中的实体批量补充全图可见连接。"""
    seeds = [chunk_seed_entities(item.payload.chunk) for item in ranked]
    connections = None
    if graph_reader is not None:
        # graph 包公开图检索，而图检索复用本模块的 Chunk 结果；运行时加载避免循环导入。
        from rag.application.graph.graph_facts import GraphFactLoader

        loader = GraphFactLoader(
            snapshot_reader=snapshot_reader,
            graph_reader=graph_reader,
            snapshot=snapshot,
        )
        # 不继承正文业务筛选；导航面向整个已授权图，读取失败不能伪装为空连接。
        connections = await loader.visible_connections(
            [seed.id for chunk_seeds in seeds for seed in chunk_seeds]
        )
    return [
        ChunkHit(
            chunk_id=item.payload.chunk.chunk_id,
            resource_id=item.payload.chunk.resource_id,
            content_revision=item.payload.chunk.content_revision,
            section_id=item.payload.chunk.section_id,
            section_path=item.payload.chunk.section_path,
            text=item.payload.chunk.raw_text,
            rank=item.rank,
            score=item.score,
            seed_entities=[
                replace(seed, relations=connections[seed.id])
                if connections is not None
                else seed
                for seed in chunk_seeds
            ],
            # 响应单独投影；种子解析、图事实回查及存储仍使用完整 metadata。
            metadata={
                key: value
                for key, value in item.payload.chunk.metadata.items()
                if key not in _CHUNK_HIT_EXCLUDED_METADATA_KEYS
            },
        )
        for item, chunk_seeds in zip(ranked, seeds, strict=True)
    ]


def _stored_embedding(payload: PreparedChunk) -> list[float]:
    # Qdrant 在读取边界校验向量；其他 VectorReader 也必须履行按需返回合同。
    if payload.embedding is None:
        raise RuntimeError("missing requested vector match embedding")
    return payload.embedding
