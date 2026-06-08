import asyncio
import hashlib
from dataclasses import dataclass
from typing import Dict, List

from chat.application.rag.domain.index_chunks import RetrieveChunk, SearchChunk
from chat.application.rag.domain.index_chunks import SearchChunkContext
from chat.application.rag.domain.ports import (
    RagContextCacheLookup,
    RagContextCacheRepository,
    RagContextCacheWrite,
)
from chat.application.rag.domain.resource_lifecycle import RagResource
from chat.application.rag.implementations.providers.context_client import ContextBuildError, RagContextClient


@dataclass(frozen=True, slots=True)
class RagContextBuilderConfig:
    """RAG context builder 配置。

    - context_model_version: 构建 context 的模型版本
    - context_prompt_version: 用于生成 Context Indexing 的 prompt
    - concurrency: 最大并发数
    """

    context_model_version: str
    context_prompt_version: str
    concurrency: int = 8


@dataclass(frozen=True, slots=True)
class ContextCacheItem:
    """Context cache 中间项。"""

    search_chunk: SearchChunk
    parent_chunk: RetrieveChunk
    context_input_hash: str


@dataclass(frozen=True, slots=True)
class GeneratedContext:
    """Context cache miss 生成结果。"""

    chunk_id: str
    context_input_hash: str
    context_text: str


class RagContextBuilder:
    """RAG Context Indexing 构建器。

    - 为每个 SearchChunk 生成 SearchChunkContext。
    - Context Indexing 永远启用。
    - 先查 context cache，cache miss 时调用 context client。
    - 任意 SearchChunk 生成 context 失败时，整个索引构建失败。
    - 返回顺序与 search_chunks 输入顺序一致。
    """

    def __init__(
            self,
            context_client: RagContextClient,
            cache_repository: RagContextCacheRepository,
            config: RagContextBuilderConfig,
    ) -> None:
        """初始化 Context 构建器。

        Args:
            context_client: 用于生成检索上下文的 LLM 客户端。
            cache_repository: 上下文缓存仓储。
            config: 构建器配置（模型版本、prompt 版本、并发数）。
        """
        self._context_client = context_client
        self._cache_repository = cache_repository
        self._config = config

    async def build_contexts(
            self,
            *,
            resource: RagResource,
            material_hash: str,
            retrieve_chunks: List[RetrieveChunk],
            search_chunks: List[SearchChunk],
    ) -> List[SearchChunkContext]:
        """构建所有 SearchChunk 的检索上下文。

        Args:
        - resource: 当前资源事实对象。
        - material_hash: 当前资源材料 hash。
        - retrieve_chunks: 父块列表。
        - search_chunks: 子块列表。

        Returns:
        - SearchChunkContext 列表。
        """
        if not search_chunks:
            return []

        parent_chunk_map = {
            chunk.chunk_id: chunk
            for chunk in retrieve_chunks
        }

        cache_items = self._build_cache_items(
            resource=resource,
            parent_chunk_map=parent_chunk_map,
            search_chunks=search_chunks,
        )

        # 命中缓存时，根据 hash 批发出库
        cached_contexts = await self._cache_repository.get_contexts(
            [
                RagContextCacheLookup(
                    lookup_id=item.search_chunk.chunk_id,
                    user_id=resource.user_id,
                    context_model_version=self._config.context_model_version,
                    context_prompt_version=self._config.context_prompt_version,
                    context_input_hash=item.context_input_hash,
                )
                for item in cache_items
            ]
        )

        context_text_by_chunk_id: Dict[str, str] = cached_contexts

        # 未命中缓存时，调用 llm 生成 context
        missing_items = [
            item
            for item in cache_items
            if item.search_chunk.chunk_id not in context_text_by_chunk_id
        ]

        generated_contexts = await self._build_missing_contexts(
            resource=resource,
            missing_items=missing_items,
        )

        # 写入缓存
        context_text_by_chunk_id.update({
            ctx.chunk_id: ctx.context_text for ctx in generated_contexts
        })

        cache_writes = [
            RagContextCacheWrite(
                user_id=resource.user_id,
                context_model_version=self._config.context_model_version,
                context_prompt_version=self._config.context_prompt_version,
                context_input_hash=ctx.context_input_hash,
                context_text=ctx.context_text,
                source_material_hash=material_hash,
                source_display_name=resource.display_name,
            )
            for ctx in generated_contexts
        ]

        await self._cache_repository.put_contexts(cache_writes)

        return [
            SearchChunkContext(
                chunk_id=search_chunk.chunk_id,
                context_text=context_text_by_chunk_id[search_chunk.chunk_id],
            )
            for search_chunk in search_chunks
        ]

    async def _build_missing_contexts(
            self,
            *,
            resource: RagResource,
            missing_items: List[ContextCacheItem],
    ) -> List[GeneratedContext]:
        """并发调用 LLM 生成未命中缓存的上下文。

        使用信号量控制并发度，逐项生成 context_text。
        生成过程中的异常（LLM 调用失败、空内容）统一包装为 ContextBuildError 抛出。

        Args:
            resource: 当前资源。
            missing_items: 未命中缓存的上下文项列表。

        Returns:
            生成的上下文列表。

        Raises:
            ContextBuildError: 任何一项生成失败时抛出。
        """
        if not missing_items:
            return []

        semaphore = asyncio.Semaphore(self._config.concurrency)

        async def build_one(item: ContextCacheItem) -> GeneratedContext:
            """构建当前流程。"""
            async with semaphore:
                # 捕获大模型/三方网关抖动、超时或限流错误，收拢异常域
                try:
                    context_text = await self._context_client.generate_context(
                        resource=resource,
                        parent_chunk=item.parent_chunk,
                        search_chunk=item.search_chunk,
                    )
                except Exception as e:
                    raise ContextBuildError(
                        f"Context client failed for search chunk {item.search_chunk.chunk_id}: {e}"
                    ) from e

            context_text = context_text.strip()
            if not context_text:
                raise ContextBuildError(
                    f"Empty context generated for search chunk: {item.search_chunk.chunk_id}"
                )

            return GeneratedContext(
                chunk_id=item.search_chunk.chunk_id,
                context_input_hash=item.context_input_hash,
                context_text=context_text,
            )

        return list(
            await asyncio.gather(
                *[
                    build_one(item)
                    for item in missing_items
                ]
            )
        )

    def _build_cache_items(
            self,
            *,
            resource: RagResource,
            parent_chunk_map: Dict[str, RetrieveChunk],
            search_chunks: List[SearchChunk],
    ) -> List[ContextCacheItem]:

        """从 SearchChunk 列表及其父块映射构建缓存项列表。

        算法步骤：
        1. 对每个 search_chunk 找到对应的 parent_chunk
        2. 基于 (user_id, resource_kind, resource_id, display_name,
           context_prompt_version, parent_chunk.text, search_chunk.text)
           拼接原始输入并计算 SHA256 hash 作为缓存指纹
        3. 返回缓存项列表供后续查缓存和写缓存使用

        Raises:
            ContextBuildError: 如果 search_chunk 找不到对应的 parent_chunk。
        """
        cache_items: List[ContextCacheItem] = []

        for search_chunk in search_chunks:
            parent_chunk = parent_chunk_map.get(search_chunk.parent_chunk_id)
            if parent_chunk is None:
                raise ContextBuildError(
                    f"Parent chunk not found: {search_chunk.parent_chunk_id}"
                )

            raw_text = "\n".join(
                [
                    resource.user_id,
                    resource.resource_kind.value,
                    resource.resource_id,
                    resource.display_name,
                    self._config.context_prompt_version,
                    parent_chunk.text,
                    search_chunk.text,
                ]
            )

            # 计算每个块的专属指纹
            context_input_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            cache_items.append(
                ContextCacheItem(
                    search_chunk=search_chunk,
                    parent_chunk=parent_chunk,
                    context_input_hash=context_input_hash,
                )
            )

        return cache_items
