from abc import ABC, abstractmethod

from chat.application.rag.permissions import build_owner_acl_projection
from chat.application.rag.runtime.indexing.indexers.keyword_indexer import ElasticsearchKeywordIndexer
from chat.application.rag.runtime.indexing.indexers.qdrant_collection import QdrantCollectionManager
from chat.application.rag.runtime.indexing.indexers.qdrant_indexer import QdrantChunkIndexer
from chat.application.rag.runtime.llm_clients.dense_embedding import DenseEmbeddingClient
from chat.application.rag.runtime.models import RagIndexManifest
from chat.application.rag.runtime.models import RagResource
from chat.application.rag.runtime.models import VersionSnapshot
from chat.application.rag.runtime.persistence.interfaces import RagChunkRepository
from chat.application.rag.runtime.persistence.interfaces import RagManifestRepository
from chat.application.rag.runtime.resources.chunker import RagChunker
from common.logger import log_event
from .context_indexing import RagContextBuilder
from .indexing_text_builder import RagIndexingTextBuilder


class RagIndexBuilder(ABC):
    """RAG 索引构建器接口。

    - 负责执行真正的索引构建。
    - 输入必须是已经通过过期检查的资源和版本快照。
    - build 成功返回后，Worker 才会 ACK 队列消息。
    - build 抛出异常时，Worker 不会 ACK。
    """

    @abstractmethod
    async def build(
        self,
        resource: RagResource,
        version_snapshot: VersionSnapshot,
    ) -> None:
        """构建指定资源版本的索引。

        Args:
            resource: 已通过消息有效性检查的资源事实对象。
            version_snapshot: 当前资源对应的索引版本快照。
        """
        pass


class RagResourceIndexBuilder(RagIndexBuilder):
    """RAG 资源索引构建器。

    - 构建单个资源版本的完整索引。
    - Context Indexing 失败时，整个索引构建失败。
    - Mongo chunks、Qdrant points、Elasticsearch docs 全部写入成功后，才发布 Manifest。
    - Manifest 发布前，线上检索仍然指向旧 index_version。
    - 当前版本写入 Qdrant dense vector / BM25 document 和 Elasticsearch keyword docs。
    """

    def __init__(
        self,
        chunker: RagChunker,
        context_builder: RagContextBuilder,
        indexing_text_builder: RagIndexingTextBuilder,
        chunk_repository: RagChunkRepository,
        dense_embedding_client: DenseEmbeddingClient,
        qdrant_collection_manager: QdrantCollectionManager,
        qdrant_chunk_indexer: QdrantChunkIndexer,
        elasticsearch_keyword_indexer: ElasticsearchKeywordIndexer,
        manifest_repository: RagManifestRepository,
    ) -> None:
        """初始化资源索引构建器。

        Args:
            chunker: 分块器。
            context_builder: 检索上下文构建器。
            indexing_text_builder: 索引文本构建器。
            chunk_repository: Chunk 仓储。
            dense_embedding_client: 稠密嵌入生成客户端。
            qdrant_collection_manager: Qdrant 集合管理器。
            qdrant_chunk_indexer: Qdrant 向量索引器。
            elasticsearch_keyword_indexer: ES 关键字索引器。
            manifest_repository: 索引清单仓储。
        """
        self._chunker = chunker
        self._context_builder = context_builder
        self._indexing_text_builder = indexing_text_builder
        self._chunk_repository = chunk_repository
        self._dense_embedding_client = dense_embedding_client
        self._qdrant_collection_manager = qdrant_collection_manager
        self._qdrant_chunk_indexer = qdrant_chunk_indexer
        self._elasticsearch_keyword_indexer = elasticsearch_keyword_indexer
        self._manifest_repository = manifest_repository

    async def build(
        self,
        resource: RagResource,
        version_snapshot: VersionSnapshot,
    ) -> None:
        """构建指定资源版本的索引。

        执行 7 步流水线：
        1. 确保 Qdrant 集合和 ES 索引可用
        2. 分块原文为 retrieve_chunk / search_chunk
        3. 为每个 search_chunk 生成检索上下文
        4. 构造 semantic_indexing_text 和 keyword_text 双索引文本
        5. 通过 dense embedding 模型生成稠密向量
        6. 依次写入 Mongo chunk store、Qdrant points、ES keyword docs
        7. 最后发布 Manifest 使新版本上线

        Args:
            resource: 已通过消息有效性检查的资源事实对象。
            version_snapshot: 当前资源对应的索引版本快照。
        """

        # 先确认外部索引容器可用，避免后续 LLM / embedding 成本白白消耗。
        await self._qdrant_collection_manager.ensure_collection()
        await self._elasticsearch_keyword_indexer.ensure_index()

        # 原文分块：retrieve_chunk 用于阅读，search_chunk 用于检索。
        chunking_result = self._chunker.build_chunks(resource)
        retrieve_chunks = chunking_result.retrieve_chunks
        search_chunks = chunking_result.search_chunks
        log_event(
            "rag indexing chunk quality",
            user_id=resource.user_id,
            resource_kind=resource.resource_kind.value,
            resource_id=resource.resource_id,
            index_version=version_snapshot.index_version,
            retrieve_chunk_count=len(retrieve_chunks),
            search_chunk_count=len(search_chunks),
            avg_retrieve_chunk_length=_avg_text_length(
                [chunk.text for chunk in retrieve_chunks]
            ),
            avg_search_chunk_length=_avg_text_length(
                [chunk.text for chunk in search_chunks]
            ),
            short_search_chunk_count=sum(
                1 for chunk in search_chunks if len(chunk.text) < 120
            ),
            search_chunks_with_heading_count=sum(
                1 for chunk in search_chunks if "Section: " in chunk.text
            ),
            fenced_code_block_count=resource.content.count("```") // 2,
            markdown_table_line_count=sum(
                1 for line in resource.content.splitlines() if "|" in line
            ),
        )

        # 为每个 search_chunk 生成检索上下文。
        # Context Indexing 永远启用，生成失败会直接中断索引构建。
        contexts = await self._context_builder.build_contexts(
            resource=resource,
            material_hash=version_snapshot.material_hash,
            retrieve_chunks=retrieve_chunks,
            search_chunks=search_chunks,
        )

        # 构造双索引文本：
        # - semantic_indexing_text 用于 dense 召回。
        # - keyword_text 保留原文，用于 keyword exact。
        indexing_text_pairs = self._indexing_text_builder.build_text_pairs(
            resource=resource,
            search_chunks=search_chunks,
            contexts=contexts,
        )

        # semantic_indexing_text 同时用于 dense embedding 和 Qdrant BM25 document。
        dense_vectors = await self._dense_embedding_client.embed_texts(
            [
                indexing_text_pairs[chunk.chunk_id].semantic_indexing_text
                for chunk in search_chunks
            ]
        )

        # 写 Mongo chunk store，保存原文块、retrieval_context 和索引文本。
        await self._chunk_repository.replace_chunks(
            user_id=resource.user_id,
            resource_kind=resource.resource_kind,
            resource_id=resource.resource_id,
            index_version=version_snapshot.index_version,
            retrieve_chunks=retrieve_chunks,
            search_chunks=search_chunks,
            contexts=contexts,
            indexing_text_pairs=indexing_text_pairs,
        )

        # 写 Qdrant chunk points，用 payload 支持线上强过滤。
        await self._qdrant_chunk_indexer.upsert_chunks(
            user_id=resource.user_id,
            index_version=version_snapshot.index_version,
            search_chunks=search_chunks,
            dense_vectors=dense_vectors,
            indexing_text_pairs=indexing_text_pairs,
        )

        # 写 Elasticsearch keyword docs，用于 keyword exact / lexical recall。
        await self._elasticsearch_keyword_indexer.upsert_keyword_chunks(
            user_id=resource.user_id,
            index_version=version_snapshot.index_version,
            display_name=resource.display_name,
            heading_path="",
            search_chunks=search_chunks,
            indexing_text_pairs=indexing_text_pairs,
        )

        # Manifest 必须最后发布。
        # 发布成功后，线上检索才会切换到新的 index_version。
        await self._manifest_repository.publish(
            RagIndexManifest(
                user_id=resource.user_id,
                resource_kind=resource.resource_kind,
                resource_id=resource.resource_id,
                resource_version=version_snapshot.resource_version,
                material_hash=version_snapshot.material_hash,
                pipeline_version=version_snapshot.pipeline_version,
                current_index_version=version_snapshot.index_version,
                acl_projection=(
                    resource.acl_projection
                    if resource.acl_projection is not None
                    else build_owner_acl_projection(resource.user_id)
                ),
            )
        )


def _avg_text_length(texts) -> int:
    if not texts:
        return 0
    return int(sum(len(text) for text in texts) / len(texts))

