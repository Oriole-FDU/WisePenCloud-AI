import hashlib
from typing import Any, Dict, List

from qdrant_client import AsyncQdrantClient, models

from chat.application.rag.domain.enums import RetrievalChannel
from chat.application.rag.domain.index_chunks import DenseVector
from chat.application.rag.domain.ports import (
    RagQueryEmbeddingCacheLookup,
    RagQueryEmbeddingCacheRepository,
    RagQueryEmbeddingCacheWrite,
)
from chat.application.rag.domain.retrieval_hits import (
    ChannelRetrievalResult,
    RagRetrievedCandidate,
)
from chat.application.rag.domain.retrieval_planning import (
    RagIndexScope,
)
from chat.application.rag.enums import ResourceKind
from chat.application.rag.implementations.persistence.qdrant.collection import (
    QdrantCollectionConfig,
)
from chat.application.rag.implementations.providers.dense import DenseEmbeddingClient


class QdrantRetrievalError(RuntimeError):
    """Qdrant 检索链路异常。"""


class QdrantChunkRetriever:
    """Qdrant 向量与文本双通道异步召回器。

    - 提供基于语义（Dense）与词频（BM25）的并行检索能力。
    - 所有检索请求必须绑定 RagIndexScope 作用域以实现多租户与多版本隔离。
    """

    def __init__(
        self,
        client: AsyncQdrantClient,
        config: QdrantCollectionConfig,
        dense_embedding_client: DenseEmbeddingClient,
        query_embedding_cache_repository: RagQueryEmbeddingCacheRepository,
        query_embedding_model_version: str,
        query_embedding_cache_ttl_days: int = 7
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._config = config
        self._dense_embedding_client = dense_embedding_client
        self._query_embedding_cache_repository = query_embedding_cache_repository
        self._query_embedding_model_version = query_embedding_model_version
        self._query_embedding_cache_ttl_days = query_embedding_cache_ttl_days

    async def retrieve_dense(
        self,
        *,
        query: str,
        scopes: List[RagIndexScope],
        top_k: int,
    ) -> ChannelRetrievalResult:
        """执行稠密向量语义召回通道（Dense Semantic Retrieval）。"""

        # 若检索范围（Scopes）为空，必须立即阻断执行。
        # 目的：避免向下游服务（Embedding/Qdrant）发起无效的 I/O 请求，降低算力损耗；
        # 同时防止因 Filter 缺省导致 Qdrant 规则引擎降级为全表无条件扫描（Full Table Scan），引发多租户越权漏洞。
        if not scopes:
            return ChannelRetrievalResult(
                channel=RetrievalChannel.DENSE_SEMANTIC,
                candidates=[],
            )

        # 将文本 Query 转化为向量形式。
        dense_vectors = await self._embed_query_with_cache(query)

        # 必须确保针对单一 Query 的向量化响应长度严格等于 1。
        if len(dense_vectors) != 1:
            raise QdrantRetrievalError("Dense query embedding result count must be 1.")

        response = await self._client.query_points(
            collection_name=self._config.collection_name,
            query=dense_vectors[0], # type: ignore
            using="dense",
            query_filter=self._build_scope_filter(scopes),
            limit=top_k,
            with_payload=True,
        )

        return ChannelRetrievalResult(
            channel=RetrievalChannel.DENSE_SEMANTIC,
            candidates=[
                self._to_candidate(
                    channel=RetrievalChannel.DENSE_SEMANTIC,
                    point=point,
                    matched_query=query
                )
                for point in response.points
            ],
        )

    async def retrieve_bm25(
        self,
        *,
        query: str,
        scopes: List[RagIndexScope],
        top_k: int,
    ) -> ChannelRetrievalResult:
        """执行稀疏向量文本召回通道（Sparse Lexical Retrieval）。"""

        if not scopes:
            return ChannelRetrievalResult(
                channel=RetrievalChannel.SPARSE_LEXICAL,
                candidates=[],
            )

        response = await self._client.query_points(
            collection_name=self._config.collection_name,
            query=models.Document( # type: ignore
                text=query,
                model="qdrant/bm25",
            ),
            using="bm25",
            query_filter=self._build_scope_filter(scopes),
            limit=top_k,
            with_payload=True,
        )

        return ChannelRetrievalResult(
            channel=RetrievalChannel.SPARSE_LEXICAL,
            candidates=[
                self._to_candidate(
                    channel=RetrievalChannel.SPARSE_LEXICAL,
                    point=point,
                    matched_query=query
                )
                for point in response.points
            ],
        )

    async def _embed_query_with_cache(self, query: str) -> DenseVector:
        """处理当前流程。"""
        query_text_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        cached_vector = await self._query_embedding_cache_repository.get_vector(
            RagQueryEmbeddingCacheLookup(
                dense_embedding_model_version=self._dense_embedding_model_version,
                query_text_hash=query_text_hash,
            )
        )
        if cached_vector is not None:
            return cached_vector

        dense_vectors = await self._dense_embedding_client.embed_texts([query])
        if len(dense_vectors) != 1:
            raise QdrantRetrievalError("Qdrant query embedding result count must be 1.")

        dense_vector = dense_vectors[0]

        await self._query_embedding_cache_repository.put_vector(
            RagQueryEmbeddingCacheWrite(
                dense_embedding_model_version=self._dense_embedding_model_version,
                query_text_hash=query_text_hash,
                query_text=query,
                vector=dense_vector
            ),
            ttl_days=self._query_embedding_cache_ttl_days
        )

        return dense_vector


    def _build_scope_filter(
        self,
        scopes: List[RagIndexScope],
    ) -> models.Filter:
        """根据多维作用域对象动态构建 Qdrant 条件过滤器。"""
        return models.Filter(
            should=[
                models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=scope.user_id),
                        ),
                        models.FieldCondition(
                            key="resource_kind",
                            match=models.MatchValue(value=scope.resource_kind.value),
                        ),
                        models.FieldCondition(
                            key="resource_id",
                            match=models.MatchValue(value=scope.resource_id),
                        ),
                        models.FieldCondition(
                            key="index_version",
                            match=models.MatchValue(value=scope.index_version),
                        ),
                    ]
                )
                for scope in scopes
            ]
        )

    def _to_candidate(
        self,
        channel: RetrievalChannel,
        point: Any,
        matched_query: str
    ) -> RagRetrievedCandidate:
        """将物理层的 Qdrant 节点数据单向映射为业务领域的候选对象模型。"""

        # Qdrant 底层在分布式极端高并发负载或主从节点异步不一致状态下，可能出现索引已命中而元数据（Payload）未加载完成的残缺状态。
        # 此处强制执行断言。拒绝空 Payload 进入下游 Reranker 或 LLM 核心链路，杜绝隐式空指针异常风险。
        if point.payload is None:
            raise QdrantRetrievalError("Qdrant point payload is missing.")

        payload: Dict[str, Any] = point.payload
        
        return RagRetrievedCandidate(
            channel=channel,
            score=float(point.score),
            user_id=payload["user_id"],
            resource_kind=ResourceKind(payload["resource_kind"]),
            resource_id=payload["resource_id"],
            index_version=payload["index_version"],
            chunk_id=payload["chunk_id"],
            parent_chunk_id=payload["parent_chunk_id"],
            parent_chunk_index=payload["parent_chunk_index"],
            chunk_index=payload["chunk_index"],
            matched_query=matched_query,
        )
