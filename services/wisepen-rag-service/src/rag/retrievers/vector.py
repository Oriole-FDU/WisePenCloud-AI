"""以字符串查询组合 dense/sparse 召回；存储查询合同只在组件内部构造。"""

from enum import StrEnum
from math import isfinite

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import (
    Candidate,
    Fusion,
    FusionRetriever,
    Retriever,
    RoundRobinFusion,
)

from rag.core.schema.filters import MetadataFilters
from rag.storage.vector import VectorMatch, VectorQuery, VectorReader

# hybrid 模式下各路未显式指定 top_k 时的默认召回量。
_DEFAULT_HYBRID_ROUTE_TOP_K = 30


class VectorRetrievalMode(StrEnum):
    """初始化时选择召回路线；仅 hybrid 使用融合策略。"""

    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


class VectorRetriever(Retriever[VectorMatch]):
    """按配置执行单路或 Common 并发融合；无请求运行状态。"""

    def __init__(
        self, *, retriever: Retriever[VectorMatch], candidate_limit: int = 60
    ) -> None:
        super().__init__(top_k=candidate_limit)
        self._retriever = retriever

    @classmethod
    def from_storage(
        cls,
        *,
        reader: VectorReader,
        embedding_client: EmbeddingClient | None = None,
        mode: VectorRetrievalMode = VectorRetrievalMode.HYBRID,
        dense_top_k: int | None = None,
        sparse_top_k: int | None = None,
        candidate_limit: int = 60,
        filters: MetadataFilters | None = None,
        include_embedding: bool = False,
        fusion: Fusion[VectorMatch] | None = None,
    ) -> "VectorRetriever":
        """固定召回模式与配置；sparse 无需 embedding。

        单路模式直接使用 candidate_limit 作为路内 top_k，保留 provider 排名和分数；
        dense_top_k / sparse_top_k 仅在 hybrid 下用于控制各路召回量。
        """
        mode = VectorRetrievalMode(mode)
        if mode is VectorRetrievalMode.HYBRID:
            dense_k = (
                _DEFAULT_HYBRID_ROUTE_TOP_K if dense_top_k is None else dense_top_k
            )
            sparse_k = (
                _DEFAULT_HYBRID_ROUTE_TOP_K if sparse_top_k is None else sparse_top_k
            )
        else:
            if dense_top_k is not None or sparse_top_k is not None:
                raise ValueError(
                    "dense_top_k and sparse_top_k are only supported in hybrid mode; "
                    "single-route modes use candidate_limit"
                )
            dense_k = sparse_k = candidate_limit

        retrievers: list[Retriever[VectorMatch]] = []
        if mode in {VectorRetrievalMode.DENSE, VectorRetrievalMode.HYBRID}:
            if embedding_client is None:
                raise ValueError("dense and hybrid modes require an embedding client")
            retrievers.append(
                _DenseRetriever(
                    reader=reader,
                    embedding_client=embedding_client,
                    top_k=dense_k,
                    filters=filters,
                    include_embedding=include_embedding,
                )
            )
        if mode in {VectorRetrievalMode.SPARSE, VectorRetrievalMode.HYBRID}:
            retrievers.append(
                _SparseRetriever(
                    reader=reader,
                    top_k=sparse_k,
                    filters=filters,
                    include_embedding=include_embedding,
                )
            )
        # 单路直接使用适配器，只有 hybrid 需要 Common 的并发与融合编排。
        retriever = retrievers[0]
        if mode is VectorRetrievalMode.HYBRID:
            retriever = FusionRetriever(
                retrievers=retrievers,
                fusion=fusion if fusion is not None else RoundRobinFusion(),
                top_k=candidate_limit,
            )
        return cls(retriever=retriever, candidate_limit=candidate_limit)

    async def retrieve(self, query: str) -> list[Candidate[VectorMatch]]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        # 单路没有融合阶段，保留 provider 排名和分数；总候选预算仍然生效。
        return (await self._retriever.retrieve(query))[: self.top_k]


class _DenseRetriever(Retriever[VectorMatch]):
    """生成本次查询 embedding 后召回；供应商响应错误与 Indexer 使用相同合同。"""

    def __init__(
        self,
        *,
        reader: VectorReader,
        embedding_client: EmbeddingClient,
        top_k: int,
        filters: MetadataFilters | None,
        include_embedding: bool,
    ) -> None:
        super().__init__(top_k=top_k)
        self._reader = reader
        self._embedding_client = embedding_client
        self._filters = filters
        self._include_embedding = include_embedding

    async def retrieve(self, query: str) -> list[Candidate[VectorMatch]]:
        embedding = await embed_query(self._embedding_client, query)

        matches = await self._reader.query(
            VectorQuery(
                embedding=embedding,
                top_k=self.top_k,
                filters=self._filters,
                include_embedding=self._include_embedding,
            )
        )
        return [
            Candidate(match.record_id, match, rank, match.score)
            for rank, match in enumerate(matches, start=1)
        ]


class _SparseRetriever(Retriever[VectorMatch]):
    """将查询原文交给仓储 sparse 路线，不生成或替换候选 embedding。"""

    def __init__(
        self,
        *,
        reader: VectorReader,
        top_k: int,
        filters: MetadataFilters | None,
        include_embedding: bool,
    ) -> None:
        super().__init__(top_k=top_k)
        self._reader = reader
        self._filters = filters
        self._include_embedding = include_embedding

    async def retrieve(self, query: str) -> list[Candidate[VectorMatch]]:
        matches = await self._reader.query(
            VectorQuery(
                text=query,
                top_k=self.top_k,
                filters=self._filters,
                include_embedding=self._include_embedding,
            )
        )
        return [
            Candidate(match.record_id, match, rank, match.score)
            for rank, match in enumerate(matches, start=1)
        ]


async def embed_query(embedding_client: EmbeddingClient, query: str) -> list[float]:
    """统一校验查询 embedding 的数量、配置维度和有限值。"""
    try:
        vectors = await embedding_client.embed(texts=[query])
    except ValueError as exc:
        raise RuntimeError("invalid embedding response") from exc
    if len(vectors) != 1 or any(
        (
            embedding_client.dimensions is not None
            and len(vector) != embedding_client.dimensions
        )
        or any(
            type(value) not in (int, float) or not isfinite(value) for value in vector
        )
        for vector in vectors
    ):
        raise RuntimeError("invalid embedding response count, dimensions or values")
    return vectors[0]
