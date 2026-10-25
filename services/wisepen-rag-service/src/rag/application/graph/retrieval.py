"""实体/关系召回形成共享图上下文，只有已授权来源 Chunk 进入精排。"""

from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import zip_longest

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import Candidate, Fusion, Reranker, RetrievalPipeline
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.application.retrieval import ChunkHit, PreparedChunk, build_chunk_hits
from rag.core.schema import GraphEdge, GraphNode
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
from .graph_facts import GraphFactLoader
from .models import GraphEntity, GraphRelationship, group_entities, group_relationships


@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    """共享图事实与精排原文并列；top_k 和 rank/score 仅作用于 chunks。"""

    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    chunks: list[ChunkHit] = field(default_factory=list)


class GraphRetrieval:
    """共享连接，每次调用独立召回、授权与来源选择，不生成关键词。"""

    def __init__(
            self,
            *,
            snapshot_reader: ResourceSnapshotReader,
            graph_reader: GraphReader,
            embedding_client: EmbeddingClient,
            entity_reader: VectorReader | None = None,
            relationship_reader: VectorReader | None = None,
            fusion: Fusion[GraphMatch] | None = None,
            reranker: Reranker[PreparedChunk] | None = None,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._graph_reader = graph_reader
        self._embedding_client = embedding_client
        self._entity_reader = entity_reader
        self._relationship_reader = relationship_reader
        self._fusion = fusion
        self._reranker = reranker

    async def retrieve(
            self,
            query: str,
            *,
            snapshot: ResourceSnapshot,
            mode: GraphRetrievalMode = GraphRetrievalMode.HYBRID,
            top_k: int = 10,
            focus: str = "",
            candidate_limit: int = 60,
            entity_top_k: int | None = None,
            relationship_top_k: int | None = None,
            neighbor_direction: GraphDirection = GraphDirection.BOTH,
            neighbor_relation_types: list[str] | None = None,
            neighbor_limit: int = 100,
            related_chunk_number: int = 5,
            filters: MetadataFilters | None = None,
    ) -> GraphRetrievalResult:
        """local 只补关系，global 只补端点；授权丢弃后不补召回。"""
        # 预算由调用方保证；这里只把方向归一为枚举。
        direction = GraphDirection(neighbor_direction)

        retriever = GraphRetriever.from_storage(
            embedding_client=self._embedding_client,
            entity_reader=self._entity_reader,
            relationship_reader=self._relationship_reader,
            mode=mode,
            entity_top_k=entity_top_k,
            relationship_top_k=relationship_top_k,
            candidate_limit=candidate_limit,
            filters=filters,
            fusion=self._fusion,
        )
        loader = GraphFactLoader(
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
            filters=filters,
        )
        context = GraphRetrievalResult()

        async def prepare(
                candidates: list[Candidate[GraphMatch]],
        ) -> list[Candidate[PreparedChunk]]:
            nonlocal context

            # 先基于已授权候选构建共享图上下文。
            context = await self._prepare(
                candidates,
                loader=loader,
                direction=direction,
                relation_types=neighbor_relation_types,
                relation_limit=neighbor_limit,
            )

            # 汇总实体/关系各自对应的来源 Chunk。
            entity_sources = {
                node.id: [source.chunk_id for source in node.sources]
                for node in context.entities
            }
            relation_sources = {
                edge.id: [source.chunk_id for source in edge.sources]
                for edge in context.relationships
            }

            # 按来源配额选 Chunk；关系路排除已选中的实体来源。
            entity_chunks = select_source_chunks(entity_sources, related_chunk_number)
            relation_chunks = select_source_chunks(
                relation_sources,
                related_chunk_number,
                excluded=set(entity_chunks),
            )

            # 两路交替合并，排序阶段不再借用实体或关系向量的分数。
            chunk_ids = dict.fromkeys(
                key
                for pair in zip_longest(entity_chunks, relation_chunks)
                for key in pair
                if key is not None
            )
            return [
                Candidate(key, PreparedChunk(loader.chunks[key], None), rank)
                for rank, key in enumerate(chunk_ids, start=1)
            ]

        pipeline = RetrievalPipeline[GraphMatch, PreparedChunk](
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=self._reranker,
        )
        ranked = await pipeline.run(query=query, top_k=top_k, focus=focus)
        chunks = await build_chunk_hits(
            ranked,
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
        )
        return replace(context, chunks=chunks)

    async def _prepare(
            self,
            candidates: list[Candidate[GraphMatch]],
            *,
            loader: GraphFactLoader,
            direction: GraphDirection,
            relation_types: list[str] | None,
            relation_limit: int,
    ) -> GraphRetrievalResult:
        """先核验来源向量，再按两种路线分别读取拓扑；投影不是权威事实。"""
        located = []
        for item in candidates:
            metadata = item.payload.vector.metadata

            # 来源元数据必须完整，且记录 ID 与 kind/chunk/fact 一致。
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
                item.payload.kind,
                metadata["chunk_id"],
                metadata["fact_id"],
            ):
                located.append(item)

        # 批量加载候选涉及的 Chunk 与图事实。
        await loader.load_chunks(
            [item.payload.vector.metadata["chunk_id"] for item in located]
        )

        local_nodes: dict[tuple[str, str], GraphNode] = {}
        global_edges: dict[tuple[str, str], GraphEdge] = {}
        for item in located:
            metadata = item.payload.vector.metadata
            chunk = loader.chunks.get(metadata["chunk_id"])

            # Chunk 必须存在，且资源与内容版本一致。
            if (
                    chunk is None
                    or chunk.resource_id != metadata["resource_id"]
                    or chunk.content_revision != metadata["content_revision"]
            ):
                continue

            nodes, edges = loader.facts[chunk.chunk_id]
            kind, fact_id = item.payload.kind, metadata["fact_id"]
            fact = (nodes if kind is GraphVectorKind.ENTITY else edges).get(fact_id)

            # 事实哈希与图版本必须匹配，防止使用过期投影。
            if (
                    fact is None
                    or graph_fact_sha256(fact, nodes) != metadata["fact_sha256"]
                    or metadata.get("graph_version") != chunk.metadata.get("graph_version")
            ):
                continue

            if kind is GraphVectorKind.ENTITY:
                local_nodes.setdefault((fact_id, chunk.chunk_id), fact)
            else:
                global_edges.setdefault((fact_id, chunk.chunk_id), fact)

        # local 路线：召回实体后补邻居关系，不把邻居描述加入实体列表。
        local_ids = list(dict.fromkeys(key[0] for key in local_nodes))
        if local_ids:
            for node in await loader.read_nodes(local_ids):
                local_nodes.setdefault((node.id, node.source_id), node)

        local_edges = []
        if local_ids and relation_types != []:
            projections = await self._graph_reader.get_neighbors(
                local_ids,
                direction=direction,
                relation_types=relation_types,
                filters=loader.filters,
                limit=relation_limit,
            )
            # 关系两端已在来源解析时核验。
            local_edges = await loader.read_edges(projections)

        # global 路线：召回关系后补端点节点。
        global_ids = list(dict.fromkeys(key[0] for key in global_edges))
        if global_ids:
            projections = await self._graph_reader.get_relations(
                global_ids, filters=loader.filters
            )
            for edge in await loader.read_edges(projections):
                global_edges.setdefault((edge.id, edge.source_ids[0]), edge)

        global_nodes: dict[tuple[str, str], GraphNode] = {}
        for edge in global_edges.values():
            # 即使缺少节点投影，合法关系来源中的端点仍是有效事实。
            nodes = loader.facts[edge.source_ids[0]][0]
            for key in (edge.source_id, edge.target_id):
                node = nodes[key]
                global_nodes.setdefault((node.id, node.source_id), node)

        endpoint_ids = list(dict.fromkeys(key[0] for key in global_nodes))
        if endpoint_ids:
            for node in await loader.read_nodes(endpoint_ids):
                global_nodes.setdefault((node.id, node.source_id), node)

        # 两路交替顺序决定逻辑事实及来源顺序；只收拢拓扑，不合并描述。
        return GraphRetrievalResult(
            group_entities(
                node
                for pair in zip_longest(local_nodes.values(), global_nodes.values())
                for node in pair
                if node is not None
            ),
            group_relationships(
                edge
                for pair in zip_longest(local_edges, global_edges.values())
                for edge in pair
                if edge is not None
            ),
        )


def select_source_chunks(
        sources: dict[str, list[str]],
        maximum: int,
        *,
        excluded: set[str] | None = None,
) -> list[str]:
    """分配来源名额。

    来源归属最早的事实，事实内部优先共现次数高的 Chunk；非空事实的
    配额从 maximum 线性递减至 1，未用配额按原顺序补给仍有来源的事实。
    """
    occurrences = Counter(
        key for keys in sources.values() for key in dict.fromkeys(keys)
    )
    seen = set() if excluded is None else set(excluded)

    # 每个事实按首次出现顺序去重并生成候选组。
    groups = []
    for keys in sources.values():
        available = []
        for key in keys:
            if key not in seen:
                available.append(key)
                seen.add(key)
        if available:
            groups.append(
                sorted(
                    available,
                    key=lambda key: occurrences[key],
                    reverse=True,
                )
            )

    # 非空事实配额从 maximum 线性递减至 1。
    selected = []
    used = []
    remaining = 0
    for index, keys in enumerate(groups):
        quota = (
            maximum
            if len(groups) == 1
            else round(maximum - index / (len(groups) - 1) * (maximum - 1))
        )
        count = min(quota, len(keys))
        selected.extend(keys[:count])
        used.append(count)
        remaining += quota - count

    # 未用配额按组顺序补给仍有候选的事实。
    for _ in range(remaining):
        for index, keys in enumerate(groups):
            if used[index] < len(keys):
                selected.append(keys[used[index]])
                used[index] += 1
                break
        else:
            break

    return selected
