from typing import Dict, Iterable, Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.indexing.models import RagIndexMessage
from chat.application.rag.runtime.models import (
    RagResource,
    ResourceUpsertResult,
    VersionSnapshot,
)
from chat.application.rag.runtime.persistence.interfaces import RagIndexingQueueRepository
from chat.application.rag.runtime.persistence.interfaces import RagManifestRepository
from .resource_handlers import RagResourceHandler
from .version_service import RagVersionService


class ResourceService:
    """RAG 资源应用服务。

    - 通过 ResourceKind 分发到对应 handler。
    - upsert 后计算 VersionSnapshot。
    - upsert 后发布索引消息。
    - delete 后删除 Manifest，让资源立即退出线上检索。
    """

    def __init__(
        self,
        handlers: Iterable[RagResourceHandler],
        manifest_repository: RagManifestRepository,
        version_service: RagVersionService,
        index_message_repository: RagIndexingQueueRepository,
    ) -> None:
        # 根据 resource_kind，路由到对应的资源处理器。
        """初始化对象依赖。"""
        self._handler_map: Dict[ResourceKind, RagResourceHandler] = {
            handler.resource_kind: handler for handler in handlers
        }
        self._manifest_repository = manifest_repository
        self._version_service = version_service
        self._index_message_repository = index_message_repository

    async def upsert(self, pending_resource: RagResource) -> ResourceUpsertResult:
        """写入或更新资源。

        Args:
        - pending_resource: 待写入或更新的资源。

        Returns:
        - 资源写入结果。
        """

        saved_resource = await self._handler_map[pending_resource.resource_kind].upsert(
            pending_resource
        )
        version_snapshot = self._version_service.build_snapshot(saved_resource)

        # 发布索引消息
        await self._index_message_repository.publish(
            _build_index_message(
                resource=saved_resource,
                version_snapshot=version_snapshot,
            )
        )

        return ResourceUpsertResult(
            resource=saved_resource,
            version_snapshot=version_snapshot,
        )

    async def get(
        self,
        resource_kind: ResourceKind,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """读取资源。

        Args:
        - resource_kind: 资源类型。
        - user_id: 用户 ID。
        - resource_id: 资源 ID。

        Returns:
        - 获取的资源实例，若不存在则返回 None。
        """

        return await self._handler_map[resource_kind].get_by_id(
            user_id=user_id,
            resource_id=resource_id,
        )

    async def delete(
        self,
        resource_kind: ResourceKind,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """删除资源并熔断线上检索。

        Args:
        - resource_kind: 资源类型。
        - user_id: 用户 ID。
        - resource_id: 资源 ID。

        Returns:
        - 被删除的资源实例，若不存在则返回 None。
        """

        deleted_resource = await self._handler_map[resource_kind].mark_deleted(
            user_id=user_id,
            resource_id=resource_id,
        )

        await self._manifest_repository.delete(
            user_id=user_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )

        return deleted_resource

    async def mark_indexing(
        self,
        resource_kind: ResourceKind,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[RagResource]:
        """标记资源进入索引中。"""
        return await self._handler_map[resource_kind].mark_indexing(
            user_id=user_id,
            resource_id=resource_id,
            index_version=index_version,
        )

    async def mark_index_success(
        self,
        resource_kind: ResourceKind,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[RagResource]:
        """标记资源索引成功。"""
        return await self._handler_map[resource_kind].mark_index_success(
            user_id=user_id,
            resource_id=resource_id,
            index_version=index_version,
        )

    async def mark_index_failed(
        self,
        resource_kind: ResourceKind,
        user_id: str,
        resource_id: str,
        index_version: str,
        error: str,
    ) -> Optional[RagResource]:
        """标记资源索引失败。"""
        return await self._handler_map[resource_kind].mark_index_failed(
            user_id=user_id,
            resource_id=resource_id,
            index_version=index_version,
            error=error,
        )


def _build_index_message(
    *,
    resource: RagResource,
    version_snapshot: VersionSnapshot,
    priority: int = 100,
) -> RagIndexMessage:
    return RagIndexMessage(
        user_id=resource.user_id,
        resource_kind=resource.resource_kind,
        resource_id=resource.resource_id,
        expected_version=version_snapshot.resource_version,
        pipeline_version=version_snapshot.pipeline_version,
        target_index_version=version_snapshot.index_version,
        priority=priority,
    )
