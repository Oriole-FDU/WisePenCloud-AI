import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable
from typing import List

from chat.application.rag.runtime.manifest_resolver import RagManifestResolver
from chat.application.rag.runtime.retrieval.channels.models import (
    ChannelRetrievalDiagnostic,
    ChannelRetrievalResult,
    RagIndexScope,
)
from chat.application.rag.runtime.retrieval.enums import (
    RetrievalChannel,
    RetrievalChannelStatus,
)
from chat.application.rag.runtime.retrieval.models import RagRetrievalQuery
from chat.application.rag.runtime.retrieval.retrievers.elasticsearch_retriever import (
    ElasticsearchKeywordRetriever,
)
from chat.application.rag.runtime.retrieval.retrievers.qdrant_retriever import QdrantChunkRetriever
from common.logger import log_event, log_fail


@dataclass(frozen=True, slots=True)
class _RetrievalTaskSpec:
    """单个检索通道任务描述。"""

    channel: RetrievalChannel
    query: str
    task: Awaitable[ChannelRetrievalResult]


@dataclass(frozen=True, slots=True)
class RagChannelRetrievalExecution:
    """多通道检索执行结果。"""

    channel_results: List[ChannelRetrievalResult]
    diagnostics: List[ChannelRetrievalDiagnostic]
    scopes: List[RagIndexScope]


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
            channel_timeout_seconds: float = 8.0,
    ) -> None:
        """初始化对象依赖。"""
        self._manifest_resolver = manifest_resolver
        self._qdrant_retriever = qdrant_retriever
        self._elasticsearch_retriever = elasticsearch_retriever
        self._channel_timeout_seconds = channel_timeout_seconds

    async def retrieve_channels(
            self,
            query: RagRetrievalQuery,
    ) -> RagChannelRetrievalExecution:
        """执行多通道候选召回。"""

        manifests = await self._manifest_resolver.resolve_user_manifests(
            user_id=query.user_id,
            group_role_map=query.group_role_map,
            resource_kinds=query.resource_kinds,
        )

        # 检索过滤范围
        scopes = [
            RagIndexScope(
                user_id=manifest.user_id,
                resource_kind=manifest.resource_kind,
                resource_id=manifest.resource_id,
                index_version=manifest.current_index_version,
                acl_projection=manifest.acl_projection,
            )
            for manifest in manifests
        ]

        # 没有已发布 Manifest 时，当前用户没有可检索索引，直接安全阻断
        if not scopes:
            log_event(
                "rag retrieval channels skipped",
                user_id=query.user_id,
                reason="no_published_manifest",
            )
            return RagChannelRetrievalExecution(
                channel_results=[],
                diagnostics=[],
                scopes=[],
            )

        active_channels = set(query.active_channels or list(RetrievalChannel))
        semantic_queries: List[str] = (
            query.semantic_queries if query.semantic_queries else [query.query]
        )
        keyword_queries: List[str] = (
            query.keyword_queries if query.keyword_queries else [query.query]
        )

        task_specs: List[_RetrievalTaskSpec] = []

        # 灌装 Dense 稠密向量检索任务
        if RetrievalChannel.DENSE_SEMANTIC in active_channels:
            for semantic_query in semantic_queries:
                task_specs.append(
                    _RetrievalTaskSpec(
                        channel=RetrievalChannel.DENSE_SEMANTIC,
                        query=semantic_query,
                        task=self._qdrant_retriever.retrieve_dense(
                            query=semantic_query,
                            scopes=scopes,
                            top_k=query.top_k,
                        ),
                    )
                )

        # 灌装 BM25 与 ES 关键词检索任务
        for keyword_query in keyword_queries:
            if RetrievalChannel.SPARSE_LEXICAL in active_channels:
                task_specs.append(
                    _RetrievalTaskSpec(
                        channel=RetrievalChannel.SPARSE_LEXICAL,
                        query=keyword_query,
                        task=self._qdrant_retriever.retrieve_bm25(
                            query=keyword_query,
                            scopes=scopes,
                            top_k=query.top_k,
                        ),
                    ),
                )
            if RetrievalChannel.KEYWORD_EXACT in active_channels:
                task_specs.append(
                    _RetrievalTaskSpec(
                        channel=RetrievalChannel.KEYWORD_EXACT,
                        query=keyword_query,
                        task=self._elasticsearch_retriever.retrieve_keyword(
                            query=keyword_query,
                            scopes=scopes,
                            top_k=query.top_k,
                        ),
                    ),
                )

        if not task_specs:
            log_event(
                "rag retrieval channels skipped",
                user_id=query.user_id,
                reason="no_active_retrieval_channels",
                active_channels=[channel.value for channel in active_channels],
            )
            return RagChannelRetrievalExecution(
                channel_results=[],
                diagnostics=[],
                scopes=scopes,
            )

        started_at = time.monotonic()
        raw_results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    spec.task,
                    timeout=self._channel_timeout_seconds,
                )
                for spec in task_specs
            ),
            return_exceptions=True,
        )

        channel_results: List[ChannelRetrievalResult] = []
        diagnostics: List[ChannelRetrievalDiagnostic] = []
        for spec, result in zip(task_specs, raw_results):
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if isinstance(result, Exception):
                status = (
                    RetrievalChannelStatus.TIMED_OUT
                    if isinstance(result, asyncio.TimeoutError)
                    else RetrievalChannelStatus.FAILED
                )
                diagnostics.append(
                    ChannelRetrievalDiagnostic(
                        channel=spec.channel,
                        query=spec.query,
                        status=status,
                        candidate_count=0,
                        scope_count=len(scopes),
                        elapsed_ms=elapsed_ms,
                        error_type=type(result).__name__,
                        error_message=str(result) or repr(result),
                    )
                )
                log_fail(
                    "rag retrieval channel failed",
                    result,
                    user_id=query.user_id,
                    channel=spec.channel.value,
                    query=spec.query,
                    scope_count=len(scopes),
                    elapsed_ms=elapsed_ms,
                )
                continue

            diagnostics.append(
                ChannelRetrievalDiagnostic(
                    channel=result.channel,
                    query=spec.query,
                    status=RetrievalChannelStatus.SUCCEEDED,
                    candidate_count=len(result.candidates),
                    scope_count=len(scopes),
                    elapsed_ms=elapsed_ms,
                )
            )
            log_event(
                "rag retrieval channel succeeded",
                user_id=query.user_id,
                channel=result.channel.value,
                query=spec.query,
                candidate_count=len(result.candidates),
                scope_count=len(scopes),
                elapsed_ms=elapsed_ms,
            )
            channel_results.append(result)

        log_event(
            "rag retrieval channels completed",
            user_id=query.user_id,
            channel_count=len(task_specs),
            success_count=len(channel_results),
            failure_count=len(task_specs) - len(channel_results),
            candidate_count=sum(len(result.candidates) for result in channel_results),
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
        )

        return RagChannelRetrievalExecution(
            channel_results=channel_results,
            diagnostics=diagnostics,
            scopes=scopes,
        )
