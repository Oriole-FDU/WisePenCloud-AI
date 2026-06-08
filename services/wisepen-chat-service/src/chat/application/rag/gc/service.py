import asyncio
from dataclasses import dataclass
from typing import List, Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.indexing.indexers.keyword_indexer import (
    ElasticsearchKeywordIndexer,
)
from chat.application.rag.runtime.indexing.indexers.qdrant_indexer import QdrantChunkIndexer
from chat.application.rag.runtime.persistence.interfaces import RagChunkRepository
from chat.application.rag.runtime.persistence.interfaces import RagManifestRepository


class RagIndexGcError(RuntimeError):
    """RAG 物理索引 GC 失败。"""


@dataclass(frozen=True, slots=True)
class RagIndexGcResult:
    """RAG index GC 结果。

    Args:
    - user_id: 用户 ID。
    - resource_kind: 资源类型。
    - resource_id: 资源 ID。
    - kept_index_version: 保留的线上 index_version；Manifest 不存在时为 None。
    - cleaned_index_versions: 本次清理掉的旧 index_version。
    """

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    kept_index_version: Optional[str]
    cleaned_index_versions: List[str]


class RagIndexGcService:
    """RAG 物理索引 GC 服务。

    - 周期 GC 从 Manifest 扫描活跃资源。
    - 只清理 Manifest 当前未指向的旧 index_version。
    - 不删除 Manifest.current_index_version。
    - 不从 chunk 集合反向 group 扫资源，避免全表扫描。
    - 不混入索引主链路。
    """

    def __init__(
        self,
        manifest_repository: RagManifestRepository,
        chunk_repository: RagChunkRepository,
        qdrant_chunk_indexer: QdrantChunkIndexer,
        elasticsearch_keyword_indexer: ElasticsearchKeywordIndexer,
    ) -> None:
        """初始化 GC 服务。

        Args:
            manifest_repository: 索引清单仓储，用于扫描活跃资源。
            chunk_repository: Chunk 仓储，用于列出和删除旧版本。
            qdrant_chunk_indexer: Qdrant 索引器，用于清理向量索引。
            elasticsearch_keyword_indexer: ES 关键字索引器，用于清理文本索引。
        """
        self._manifest_repository = manifest_repository
        self._chunk_repository = chunk_repository
        self._qdrant_chunk_indexer = qdrant_chunk_indexer
        self._elasticsearch_keyword_indexer = elasticsearch_keyword_indexer

    async def sweep_unpublished_versions(
        self,
        *,
        limit: int = 100,
    ) -> List[RagIndexGcResult]:
        """周期扫描并清理未发布旧版本。

        - 扫描源是 Manifest 表。
        - 每个 Manifest 对应一个当前活跃资源。
        - 对每个资源清理非 current_index_version 的旧物理索引。
        """

        manifests = await self._manifest_repository.list_active_manifests(
            limit=limit,
        )

        results: List[RagIndexGcResult] = []

        for manifest in manifests:
            result = await self.cleanup_unpublished_versions(
                user_id=manifest.user_id,
                resource_kind=manifest.resource_kind,
                resource_id=manifest.resource_id,
            )
            results.append(result)

        return results

    async def cleanup_unpublished_versions(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> RagIndexGcResult:
        """清理指定资源未被 Manifest 指向的旧物理索引版本。

        - Manifest 不存在时不清理。
        - Manifest 存在时只保留 current_index_version。
        - 用于周期 GC 和手动 GC。
        """

        manifest = await self._manifest_repository.get_by_resource(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        if manifest is None:
            return RagIndexGcResult(
                user_id=user_id,
                resource_kind=resource_kind,
                resource_id=resource_id,
                kept_index_version=None,
                cleaned_index_versions=[],
            )

        index_versions = await self._chunk_repository.list_index_versions(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        cleanup_versions = [
            index_version
            for index_version in index_versions
            if index_version != manifest.current_index_version
        ]

        await self._cleanup_versions(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            index_versions=cleanup_versions,
        )

        return RagIndexGcResult(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            kept_index_version=manifest.current_index_version,
            cleaned_index_versions=cleanup_versions,
        )

    async def cleanup_deleted_resource_versions(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> RagIndexGcResult:
        """清理已删除资源的全部物理索引版本。

        - 只有 Manifest 已不存在时才允许清理全部版本。
        - 如果 Manifest 仍存在，直接拒绝，避免误删线上索引。
        """

        manifest = await self._manifest_repository.get_by_resource(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        if manifest is not None:
            raise RagIndexGcError(
                "Cannot cleanup all versions while manifest still points to an online index."
            )

        index_versions = await self._chunk_repository.list_index_versions(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        await self._cleanup_versions(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            index_versions=index_versions,
        )

        return RagIndexGcResult(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            kept_index_version=None,
            cleaned_index_versions=index_versions,
        )

    async def cleanup_exact_index_version(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
    ) -> RagIndexGcResult:
        """清理指定 index_version。

        - 如果 index_version 是 Manifest 当前线上版本，直接拒绝。
        - 用于手动修复失败构建残留。
        """

        if not index_version:
            raise ValueError("index_version must not be empty.")

        manifest = await self._manifest_repository.get_by_resource(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        if manifest is not None and manifest.current_index_version == index_version:
            raise RagIndexGcError("Cannot cleanup manifest current_index_version.")

        await self._cleanup_versions(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            index_versions=[index_version],
        )

        return RagIndexGcResult(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            kept_index_version=(
                manifest.current_index_version if manifest is not None else None
            ),
            cleaned_index_versions=[index_version],
        )

    async def _cleanup_versions(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_versions: List[str],
    ) -> None:
        """批量清理指定资源的多个旧索引版本。

        先校验所有 index_version 非空，再逐个调用 _cleanup_one_version。
        """
        if not index_versions:
            return

        for index_version in index_versions:
            if not index_version:
                raise RagIndexGcError("index_version must not be empty.")

        for index_version in index_versions:
            await self._cleanup_one_version(
                user_id=user_id,
                resource_kind=resource_kind,
                resource_id=resource_id,
                index_version=index_version,
            )

    async def _cleanup_one_version(
        self,
        *,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
    ) -> None:
        """清理单个旧 index_version。

        - 先删外部索引。
        - 外部索引删除成功后再删 Mongo chunks。
        - 如果外部删除失败，Mongo 保留，后续可重试。
        - 删除操作按 index_version 幂等执行。
        """

        # 三方索引如果由于网络抽风清理失败，绝对不提前清除元数据
        try:
            await asyncio.gather(
                self._qdrant_chunk_indexer.delete_by_index_version(
                    user_id=user_id,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    index_version=index_version,
                ),
                self._elasticsearch_keyword_indexer.delete_by_index_version(
                    user_id=user_id,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    index_version=index_version,
                ),
            )
        except Exception as e:
            raise RagIndexGcError(
                f"External index storage (Qdrant/Elasticsearch) cleanup failed "
                f"for version {index_version}. Details: {repr(e)}"
            ) from e

        await self._chunk_repository.delete_chunks_by_index_version(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            index_version=index_version,
        )
