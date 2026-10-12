"""图实体/关系来源种子召回；授权与拓扑扩展属于 Pipeline 准备阶段。"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import (
    Candidate,
    Fusion,
    Retriever,
    RoundRobinFusion,
)

from rag.core.schema.filters import MetadataFilters
from rag.retrievers.vector import embed_query
from rag.storage.graph import GraphVectorKind
from rag.storage.vector import VectorMatch, VectorQuery, VectorReader


class GraphRetrievalMode(StrEnum):
    """只选择种子路线；hybrid 是实体/关系融合，不包含普通 Chunk 召回。"""

    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class GraphMatch:
    """未经授权的来源向量候选；kind 决定准备阶段查找哪类 Mongo 图事实。"""

    kind: GraphVectorKind
    vector: VectorMatch  # 保留定位、文本摘要与可选向量；不携带图描述或原文。


class GraphRetriever(Retriever[GraphMatch]):
    """初始化固定路线、预算与融合；查询调用只接收字符串，无请求状态。"""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        routes: list[tuple[GraphVectorKind, VectorReader, int]],
        candidate_limit: int = 60,
        filters: MetadataFilters | None = None,
        include_embedding: bool = False,
        fusion: Fusion[GraphMatch] | None = None,
    ) -> None:
        super().__init__(top_k=candidate_limit)
        self._embedding_client = embedding_client
        self._routes = routes  # 固定路线：种类 / reader / 预算
        self._filters = filters  # 候选过滤，非授权
        self._include_embedding = include_embedding
        self._fusion = fusion  # hybrid 下的融合器

    @classmethod
    def from_storage(
        cls,
        *,
        embedding_client: EmbeddingClient,
        entity_reader: VectorReader | None = None,
        relationship_reader: VectorReader | None = None,
        mode: GraphRetrievalMode = GraphRetrievalMode.HYBRID,
        entity_top_k: int | None = None,
        relationship_top_k: int | None = None,
        candidate_limit: int = 60,
        filters: MetadataFilters | None = None,
        include_embedding: bool = False,
        fusion: Fusion[GraphMatch] | None = None,
    ) -> "GraphRetriever":
        """单路用总预算，hybrid 默认各 30 条，默认 Common RoundRobin 融合。

        只要求选中路线的 reader。直接 embedding query，不调用关键词生成；
        filters 是候选过滤，不是授权决定。拓扑读取在授权准备阶段进行。
        """
        mode = GraphRetrievalMode(mode)

        if type(candidate_limit) is not int or candidate_limit <= 0:
            raise ValueError("candidate_limit must be a positive integer")
        if embedding_client is None:
            raise ValueError("graph retrieval requires an embedding client")
        # 单路模式下不允许单独指定某路预算
        if mode is not GraphRetrievalMode.HYBRID and (
            entity_top_k is not None or relationship_top_k is not None
        ):
            raise ValueError("route budgets are only supported in hybrid mode")

        routes = []
        for selected, kind, reader, budget in (
            (
                mode in (GraphRetrievalMode.LOCAL, GraphRetrievalMode.HYBRID),
                GraphVectorKind.ENTITY,
                entity_reader,
                entity_top_k,
            ),
            (
                mode in (GraphRetrievalMode.GLOBAL, GraphRetrievalMode.HYBRID),
                GraphVectorKind.RELATIONSHIP,
                relationship_reader,
                relationship_top_k,
            ),
        ):
            if not selected:
                continue
            if reader is None:
                raise ValueError(f"{kind} vector reader is required")

            # hybrid 每路默认 30 条；单路直接用总预算
            top_k = (
                (30 if budget is None else budget)
                if mode is GraphRetrievalMode.HYBRID
                else candidate_limit
            )
            if type(top_k) is not int or top_k <= 0:
                raise ValueError("route budgets must be positive integers")
            routes.append((kind, reader, top_k))

        return cls(
            embedding_client=embedding_client,
            routes=routes,
            candidate_limit=candidate_limit,
            filters=filters,
            include_embedding=include_embedding,
            # 仅 hybrid 需要融合器
            fusion=(fusion if fusion is not None else RoundRobinFusion())
            if mode is GraphRetrievalMode.HYBRID
            else None,
        )

    async def retrieve(self, query: str) -> list[Candidate[GraphMatch]]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")

        # 两路检索同一 query，只生成一次向量；请求材料不留在共享实例。
        embedding = await embed_query(self._embedding_client, query)

        # 各路线并行查询
        matches = await asyncio.gather(
            *(
                reader.query(
                    VectorQuery(
                        embedding=embedding,
                        top_k=top_k,
                        filters=self._filters,
                        include_embedding=self._include_embedding,
                    )
                )
                for _, reader, top_k in self._routes
            )
        )

        # 按路线把结果包装成候选
        result_sets = []
        for (kind, _, _), result in zip(self._routes, matches, strict=True):
            result_sets.append(
                [
                    Candidate(
                        # 种类与来源记录共同决定融合身份，不按逻辑图 ID 合并来源。
                        candidate_id=f"{kind}:{match.record_id}",
                        payload=GraphMatch(kind, match),
                        rank=rank,
                        score=match.score,
                    )
                    for rank, match in enumerate(result, start=1)
                ]
            )

        # 有融合器则融合；否则单路直接输出
        candidates = (
            self._fusion.fuse(result_sets=result_sets)
            if self._fusion is not None
            else result_sets[0]
        )
        return candidates[: self.top_k]
