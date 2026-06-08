import asyncio
from typing import List

from chat.application.rag.domain.retrieval_hits import ChannelRetrievalResult
from chat.application.rag.domain.retrieval_planning import (
    RagIndexScope,
    RagRetrievalQuery,
)
from .elasticsearch_retriever import (
    ElasticsearchKeywordRetriever,
)
from .manifest_resolver import RagManifestResolver
from .qdrant_retriever import QdrantChunkRetriever


class RagRetrievalOrchestrator:
    """RAG 多通道检索编排服务。

    - 读取当前用户已发布 Manifest。
    - 基于 Manifest 构造线上索引范围。
    - 并发调用 dense / bm25 / keyword 三个召回通道。
    - 不负责候选融合。
    - 不负责 evidence 组装。
    - 不负责 rerank。
    """

    def __init__(
            self,
            manifest_resolver: RagManifestResolver,
            qdrant_retriever: QdrantChunkRetriever,
            elasticsearch_retriever: ElasticsearchKeywordRetriever,
    ) -> None:
        """初始化对象依赖。"""
        self._manifest_resolver = manifest_resolver
        self._qdrant_retriever = qdrant_retriever
        self._elasticsearch_retriever = elasticsearch_retriever

    async def retrieve_channels(
            self,
            query: RagRetrievalQuery,
    ) -> List[ChannelRetrievalResult]:
        """执行多通道候选召回。"""

        manifests = await self._manifest_resolver.resolve_user_manifests(
            user_id=query.user_id,
            resource_kinds=query.resource_kinds,
        )

        # 检索过滤范围
        scopes = [
            RagIndexScope(
                user_id=manifest.user_id,
                resource_kind=manifest.resource_kind,
                resource_id=manifest.resource_id,
                index_version=manifest.current_index_version,
            )
            for manifest in manifests
        ]

        # 没有已发布 Manifest 时，当前用户没有可检索索引，直接安全阻断
        if not scopes:
            return []

        semantic_queries: List[str] = query.semantic_queries if query.semantic_queries else [query.query,]
        keyword_queries: List[str] = query.keyword_queries if query.keyword_queries else [query.query,]

        tasks = []

        # 灌装 Dense 稠密向量检索任务
        for semantic_query in semantic_queries:
            tasks.append(
                self._qdrant_retriever.retrieve_dense(
                    query=semantic_query,
                    scopes=scopes,
                    top_k=query.top_k,
                )
            )

        # 灌装 BM25 与 ES 关键词检索任务
        for keyword_query in keyword_queries:
            tasks.append(
                self._qdrant_retriever.retrieve_bm25(
                    query=keyword_query,
                    scopes=scopes,
                    top_k=query.top_k,
                )
            )
            tasks.append(
                self._elasticsearch_retriever.retrieve_keyword(
                    query=keyword_query,
                    scopes=scopes,
                    top_k=query.top_k,
                )
            )

        channel_results = await asyncio.gather(*tasks)

        return list(channel_results)
