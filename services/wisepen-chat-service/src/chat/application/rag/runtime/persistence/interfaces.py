from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import RagGroupRole
from chat.application.rag.runtime.indexing.models import RagIndexMessage
from chat.application.rag.runtime.models import (
    DenseVector,
    IndexingTextPair,
    RagIndexManifest,
    RagResource,
    RetrieveChunk,
    SearchChunk,
    SearchChunkContext,
)


@dataclass(frozen=True, slots=True)
class RagSearchChunkRecord:
    """搜索块完整记录。

    包含 SearchChunk 及其索引时使用的上下文文本和索引文本对。
    """

    chunk: SearchChunk
    retrieval_context: str
    semantic_indexing_text: str
    keyword_text: str


@dataclass(frozen=True, slots=True)
class RagSearchChunkLookup:
    """搜索块查询参数。"""

    lookup_id: str
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class RagRetrieveChunkLookup:
    """检索块查询参数。"""

    lookup_id: str
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class RagNeighborChunkLookup:
    """邻居检索块查询参数。

    用于按中心 chunk index 获取前后 N 个相邻块。
    """

    lookup_id: str
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    center_chunk_index: int
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class RagContextCacheLookup:
    """RAG 上下文缓存查询参数。"""

    lookup_id: str
    user_id: str
    context_model_version: str
    context_prompt_version: str
    context_input_hash: str


@dataclass(frozen=True, slots=True)
class RagContextCacheWrite:
    """RAG 上下文缓存写入参数。"""

    user_id: str
    context_model_version: str
    context_prompt_version: str
    context_input_hash: str
    context_text: str
    source_material_hash: str
    source_display_name: str


@dataclass(frozen=True, slots=True)
class RagDenseEmbeddingCacheLookup:
    """RAG 稠密嵌入缓存查询参数。"""

    lookup_id: str
    dense_embedding_model_version: str
    text_hash: str


@dataclass(frozen=True, slots=True)
class RagDenseEmbeddingCacheWrite:
    """RAG 稠密嵌入缓存写入参数。"""

    dense_embedding_model_version: str
    text_hash: str
    vector: DenseVector


@dataclass(frozen=True, slots=True)
class RagQueryEmbeddingCacheLookup:
    """RAG 查询嵌入缓存查询参数。"""

    dense_embedding_model_version: str
    query_text_hash: str


@dataclass(frozen=True, slots=True)
class RagQueryEmbeddingCacheWrite:
    """RAG 查询嵌入缓存写入参数。"""

    dense_embedding_model_version: str
    query_text_hash: str
    query_text: str
    vector: DenseVector



class RagResourceRepository(ABC):
    """RAG 资源仓储接口（抽象基类）。

    定义资源读取和软删除所需的存储能力，
    由基础设施层实现具体的数据访问逻辑。
    """

    @abstractmethod
    async def get_by_id(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """根据用户 ID 和资源 ID 获取资源。

        Args:
            user_id: 资源所属用户 ID。
            resource_id: 资源唯一标识。

        Returns:
            如果资源存在返回 RagResource，否则返回 None。
        """
        pass

    @abstractmethod
    async def mark_deleted(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """将资源标记为已删除（软删除）。

        Args:
            user_id: 资源所属用户 ID。
            resource_id: 资源唯一标识。

        Returns:
            标记删除后的 RagResource，如果不存在返回 None。
        """
        pass


class NoteResourceRepository(RagResourceRepository):
    """笔记资源仓储接口。

    在 RagResourceRepository 基础上增加笔记特有的 upsert 写入能力。
    """

    @abstractmethod
    async def upsert(
        self,
        resource: RagResource,
    ) -> RagResource:
        """写入或更新笔记资源。

        Args:
            resource: 笔记资源事实。

        Returns:
            写入或更新后的 RagResource。
        """
        pass


class DocumentResourceRepository(RagResourceRepository):
    """文档资源仓储接口。

    在 RagResourceRepository 基础上增加文档特有的 upsert 写入能力。
    """

    @abstractmethod
    async def upsert(
        self,
        resource: RagResource,
    ) -> RagResource:
        """写入或更新文档资源。

        Args:
            resource: 文档资源事实。

        Returns:
            写入或更新后的 RagResource。
        """
        pass


class RagManifestRepository(ABC):
    """RAG 索引清单仓储接口。

    管理索引发布清单的存储，包括查询、发布、删除和列表操作。
    """

    @abstractmethod
    async def get_by_resource(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> Optional[RagIndexManifest]:
        """获取指定资源的最新索引清单。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。

        Returns:
            索引清单，不存在时返回 None。
        """
        pass

    @abstractmethod
    async def publish(
        self,
        manifest: RagIndexManifest,
    ) -> RagIndexManifest:
        """发布（写入）索引清单。

        Args:
            manifest: 待发布的索引清单。

        Returns:
            发布后的索引清单。
        """
        pass

    @abstractmethod
    async def delete(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> None:
        """删除指定资源的索引清单。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
        """
        pass

    @abstractmethod
    async def list_by_user(
        self,
        user_id: str,
    ) -> List[RagIndexManifest]:
        """列出指定用户的所有索引清单。

        Args:
            user_id: 用户 ID。

        Returns:
            该用户的所有索引清单列表。
        """
        pass

    @abstractmethod
    async def list_visible_manifests(
        self,
        user_id: str,
        group_role_map: Dict[str, RagGroupRole],
        resource_kinds: Optional[List[ResourceKind]] = None,
    ) -> List[RagIndexManifest]:
        """按本地 ACL 投影列出当前用户可读的索引清单。

        Args:
            user_id: 当前访问用户 ID。
            group_role_map: 当前用户在各 group 下的角色快照。
            resource_kinds: 可选资源类型过滤。

        Returns:
            当前用户拥有 VIEW 权限的索引清单列表。
        """
        pass

    @abstractmethod
    async def list_active_manifests(
        self,
        limit: int = 100,
    ) -> List[RagIndexManifest]:
        """列出当前活跃的索引清单（全量）。

        Args:
            limit: 最大返回条数限制。

        Returns:
            活跃索引清单列表。
        """
        pass




class RagChunkRepository(ABC):
    """RAG Chunk 仓储接口。

    定义 Chunk（包括 RetrieveChunk、SearchChunk、SearchChunkContext）的
    增删改查能力，由基础设施层实现具体的数据访问逻辑。
    """

    @abstractmethod
    async def replace_chunks(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
        retrieve_chunks: List[RetrieveChunk],
        search_chunks: List[SearchChunk],
        contexts: List[SearchChunkContext],
        indexing_text_pairs: Dict[str, IndexingTextPair],
    ) -> None:
        """原子性地替换指定索引版本下的所有 chunk 数据。

        先删除旧版本数据，再批量写入新的 chunk、上下文和索引文本对。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
            index_version: 索引版本标识。
            retrieve_chunks: 检索块列表。
            search_chunks: 搜索块列表。
            contexts: 搜索块上下文列表。
            indexing_text_pairs: chunk_id 到索引文本对的映射。
        """
        pass

    @abstractmethod
    async def get_retrieve_chunk(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
        chunk_id: str,
    ) -> Optional[RetrieveChunk]:
        """获取单个检索块。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
            index_version: 索引版本标识。
            chunk_id: 块 ID。

        Returns:
            检索块，不存在时返回 None。
        """
        pass

    @abstractmethod
    async def get_search_chunk_record(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
        chunk_id: str,
    ) -> Optional[RagSearchChunkRecord]:
        """获取单个搜索块完整记录。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
            index_version: 索引版本标识。
            chunk_id: 块 ID。

        Returns:
            搜索块完整记录，不存在时返回 None。
        """
        pass

    @abstractmethod
    async def get_neighbor_retrieve_chunks(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
        center_chunk_index: int,
        before: int,
        after: int,
    ) -> List[RetrieveChunk]:
        """获取指定中心块前后的相邻检索块。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
            index_version: 索引版本标识。
            center_chunk_index: 中心块的 chunk index。
            before: 向前取几个块。
            after: 向后取几个块。

        Returns:
            相邻检索块列表。
        """
        pass

    @abstractmethod
    async def get_search_chunk_records(
        self,
        lookups: List[RagSearchChunkLookup],
    ) -> Dict[str, RagSearchChunkRecord]:
        """批量获取搜索块完整记录。

        Args:
            lookups: 搜索块查询参数列表。

        Returns:
            lookup_id 到搜索块完整记录的映射。
        """
        pass

    @abstractmethod
    async def get_retrieve_chunks(
        self,
        lookups: List[RagRetrieveChunkLookup],
    ) -> Dict[str, RetrieveChunk]:
        """批量获取检索块。

        Args:
            lookups: 检索块查询参数列表。

        Returns:
            lookup_id 到检索块的映射。
        """
        pass

    @abstractmethod
    async def get_neighbor_retrieve_chunks_batch(
        self,
        lookups: List[RagNeighborChunkLookup],
    ) -> Dict[str, List[RetrieveChunk]]:
        """批量获取相邻检索块。

        Args:
            lookups: 邻居块查询参数列表。

        Returns:
            lookup_id 到相邻检索块列表的映射。
        """
        pass

    @abstractmethod
    async def list_index_versions(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> List[str]:
        """列出指定资源的所有索引版本。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。

        Returns:
            索引版本标识列表。
        """
        pass

    @abstractmethod
    async def delete_chunks_by_index_version(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
    ) -> None:
        """删除指定索引版本下的所有 chunk 数据。

        Args:
            user_id: 资源所属用户 ID。
            resource_kind: 资源类型。
            resource_id: 资源唯一标识。
            index_version: 索引版本标识。
        """
        pass




class RagContextCacheRepository(ABC):
    """RAG 上下文缓存仓储接口。

    提供上下文组装结果的缓存能力，避免相同输入重复调用 LLM。
    """

    @abstractmethod
    async def get_contexts(
        self,
        lookups: List[RagContextCacheLookup],
    ) -> Dict[str, str]:
        """批量查询缓存的上下文文本。

        Args:
            lookups: 上下文缓存查询参数列表。

        Returns:
            lookup_id 到缓存上下文文本的映射（未命中时不含该键）。
        """
        pass

    @abstractmethod
    async def put_contexts(
        self,
        writes: List[RagContextCacheWrite],
    ) -> None:
        """批量写入缓存的上下文文本。

        Args:
            writes: 上下文缓存写入参数列表。
        """
        pass


class RagDenseEmbeddingCacheRepository(ABC):
    """RAG 稠密嵌入缓存仓储接口。

    提供 Chunk 稠密向量嵌入的缓存能力，避免重复计算。
    """

    @abstractmethod
    async def get_vectors(
        self,
        lookups: List[RagDenseEmbeddingCacheLookup],
    ) -> Dict[str, DenseVector]:
        """批量查询缓存的稠密向量。

        Args:
            lookups: 稠密嵌入缓存查询参数列表。

        Returns:
            lookup_id 到稠密向量的映射（未命中时不含该键）。
        """
        pass

    @abstractmethod
    async def put_vectors(
        self,
        writes: List[RagDenseEmbeddingCacheWrite],
    ) -> None:
        """批量写入缓存的稠密向量。

        Args:
            writes: 稠密嵌入缓存写入参数列表。
        """
        pass


class RagQueryEmbeddingCacheRepository(ABC):
    """RAG 查询嵌入缓存仓储接口。

    提供查询文本稠密向量嵌入的缓存能力，避免重复计算。
    """

    @abstractmethod
    async def get_vector(
        self,
        lookup: RagQueryEmbeddingCacheLookup,
    ) -> Optional[DenseVector]:
        """查询缓存的查询嵌入向量。

        Args:
            lookup: 查询嵌入缓存查询参数。

        Returns:
            稠密向量，未命中时返回 None。
        """
        pass

    @abstractmethod
    async def put_vector(
        self,
        write: RagQueryEmbeddingCacheWrite,
        ttl_days: int,
    ) -> None:
        """写入缓存的查询嵌入向量并设置 TTL。

        Args:
            write: 查询嵌入缓存写入参数。
            ttl_days: 缓存过期天数。
        """
        pass


@dataclass(frozen=True, slots=True)
class RagQueuedIndexMessage:
    """已入队的索引消息（含消息 ID）。"""

    message_id: str
    message: RagIndexMessage
    attempts: int = 0


class RagIndexingQueueRepository(ABC):
    """RAG 索引队列仓储接口。

    定义索引消息的发布、消费和确认能力，基于消息队列实现异步索引。
    """

    @abstractmethod
    async def publish(self, message: RagIndexMessage) -> None:
        """发布一条索引消息到队列。

        Args:
            message: 待发布的索引消息。
        """
        pass

    @abstractmethod
    async def read_batch(
        self,
        consumer_group: str,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> List[RagQueuedIndexMessage]:
        """从队列批量读取待处理的消息（阻塞等待）。

        Args:
            consumer_group: 消费者组名。
            consumer_name: 消费者名。
            count: 批量读取数量。
            block_ms: 阻塞等待超时时间（毫秒）。

        Returns:
            待处理的索引消息列表。
        """
        pass

    @abstractmethod
    async def ack(
        self,
        message_id: str,
        consumer_group: str,
    ) -> None:
        """确认单条消息已处理完成。

        Args:
            message_id: 消息 ID。
            consumer_group: 消费者组名。
        """
        pass

    @abstractmethod
    async def read_stale_pending_batch(
        self,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int = 60000,
        count: int = 10,
    ) -> List[RagQueuedIndexMessage]:
        """读取超时未确认的陈旧消息（用于重新分配）。

        Args:
            consumer_group: 消费者组名。
            consumer_name: 消费者名。
            min_idle_ms: 最小空闲时间阈值（毫秒）。
            count: 批量读取数量。

        Returns:
            陈旧待确认的索引消息列表。
        """
        pass

    @abstractmethod
    async def ack_many(
        self,
        message_ids: List[str],
        consumer_group: str,
    ) -> None:
        """批量确认多条消息已处理完成。

        Args:
            message_ids: 消息 ID 列表。
            consumer_group: 消费者组名。
        """
        pass

    @abstractmethod
    async def mark_indexing(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[RagResource]:
        """标记资源进入索引中。"""
        pass

    @abstractmethod
    async def mark_index_success(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[RagResource]:
        """标记资源索引成功。"""
        pass

    @abstractmethod
    async def mark_index_failed(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
        error: str,
    ) -> Optional[RagResource]:
        """标记资源索引失败。"""
        pass

    @abstractmethod
    async def handle_failure(
        self,
        queued_message: RagQueuedIndexMessage,
        consumer_group: str,
        error: str,
        max_attempts: int,
    ) -> None:
        """处理失败消息：重试或进入死信队列，并确认原消息。"""
        pass
