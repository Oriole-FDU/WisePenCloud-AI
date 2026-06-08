from abc import ABC, abstractmethod
from typing import Optional

from chat.application.rag.domain.ports import (
    DocumentResourceRepository,
    NoteResourceRepository,
)
from chat.application.rag.domain.resource_lifecycle import RagResource
from chat.application.rag.enums import ResourceKind


class RagResourceHandler(ABC):
    """RAG 资源类型处理器。

    - 每种资源类型一个 handler。
    - ResourceService 只通过 resource_kind 分发。
    """

    @property
    @abstractmethod
    def resource_kind(self) -> ResourceKind:
        """处理当前流程。"""
        pass

    @abstractmethod
    async def upsert(self, pending_resource: RagResource) -> RagResource:
        """写入或更新资源。

        Args:
        - pending_resource: 待写入或更新的资源。

        Returns:
        - RagResource: 写入或更新后的资源实例。
        """
        pass

    @abstractmethod
    async def get_by_id(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """根据 ID 读取资源。

        Args:
        - user_id: 用户 ID。
        - resource_id: 资源 ID。

        Returns:
        - 获取的资源实例，若不存在则返回 None。
        """
        pass

    @abstractmethod
    async def mark_deleted(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """将资源标记为删除。

        Args:
        - user_id: 用户 ID。
        - resource_id: 资源 ID。

        Returns:
        - 标记删除的资源实例，若不存在则返回 None。
        """
        pass


class NoteResourceHandler(RagResourceHandler):
    """note 资源处理器。"""

    def __init__(self, repository: NoteResourceRepository) -> None:
        """初始化对象依赖。"""
        self._repository = repository

    @property
    def resource_kind(self) -> ResourceKind:
        """处理当前流程。"""
        return ResourceKind.NOTE

    async def upsert(self, pending_resource: RagResource) -> RagResource:
        """写入或更新当前流程。"""
        return await self._repository.upsert(
            user_id=pending_resource.user_id,
            resource_id=pending_resource.resource_id,
            content=pending_resource.content,
            title=pending_resource.title,
        )

    async def get_by_id(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """获取当前流程。"""
        return await self._repository.get_by_id(
            user_id=user_id,
            resource_id=resource_id,
        )

    async def mark_deleted(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """处理当前流程。"""
        return await self._repository.mark_deleted(
            user_id=user_id,
            resource_id=resource_id,
        )


class DocumentResourceHandler(RagResourceHandler):
    """document 资源处理器。"""

    def __init__(self, repository: DocumentResourceRepository) -> None:
        """初始化对象依赖。"""
        self._repository = repository

    @property
    def resource_kind(self) -> ResourceKind:
        """处理当前流程。"""
        return ResourceKind.DOCUMENT

    async def upsert(self, pending_resource: RagResource) -> RagResource:
        """写入或更新当前流程。"""
        return await self._repository.upsert(
            user_id=pending_resource.user_id,
            resource_id=pending_resource.resource_id,
            content=pending_resource.content,
            document_name=pending_resource.document_name,
        )

    async def get_by_id(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """获取当前流程。"""
        return await self._repository.get_by_id(
            user_id=user_id,
            resource_id=resource_id,
        )

    async def mark_deleted(
        self,
        user_id: str,
        resource_id: str,
    ) -> Optional[RagResource]:
        """处理当前流程。"""
        return await self._repository.mark_deleted(
            user_id=user_id,
            resource_id=resource_id,
        )
