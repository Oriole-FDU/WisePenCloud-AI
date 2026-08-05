from __future__ import annotations

from dataclasses import dataclass

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.application.rag.repositories import RagResourceSnapshotRepository
from rag.application.rag.retrieval import RagPermissionScope

from .models import RagResourceContentReadResult, RagResourceSnapshot


class RagResourceSnapshotNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RagPageContentRequest:
    resource_id: str
    page_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RagSectionContentRequest:
    resource_id: str
    section_ids: tuple[str, ...]


class RagResourceSnapshotService:
    """资源副本索引和正文读取编排。"""

    __slots__ = ("_permission_authorizer", "_repository")

    def __init__(
        self,
        *,
        permission_authorizer: RagPermissionAuthorizer,
        repository: RagResourceSnapshotRepository,
    ) -> None:
        self._permission_authorizer = permission_authorizer
        self._repository = repository

    async def snapshot(
        self,
        *,
        resource_id: str,
        scope: RagPermissionScope,
    ) -> RagResourceSnapshot:
        await self._ensure_access(resource_id, scope=scope)
        snapshot = await self._repository.load_applied_resource_snapshot(
            resource_id=resource_id
        )
        if snapshot is None:
            raise RagResourceSnapshotNotFoundError(resource_id)
        return snapshot

    async def read_pages(
        self,
        *,
        request: RagPageContentRequest,
        scope: RagPermissionScope,
    ) -> RagResourceContentReadResult:
        await self._ensure_access(request.resource_id, scope=scope)
        result = await self._repository.read_applied_page_content(
            resource_id=request.resource_id,
            page_labels=request.page_labels,
        )
        if result is None:
            raise RagResourceSnapshotNotFoundError(request.resource_id)
        return result

    async def read_sections(
        self,
        *,
        request: RagSectionContentRequest,
        scope: RagPermissionScope,
    ) -> RagResourceContentReadResult:
        await self._ensure_access(request.resource_id, scope=scope)
        result = await self._repository.read_applied_section_content(
            resource_id=request.resource_id,
            section_ids=request.section_ids,
        )
        if result is None:
            raise RagResourceSnapshotNotFoundError(request.resource_id)
        return result

    async def _ensure_access(self, resource_id: str, *, scope: RagPermissionScope) -> None:
        accessible = await self._permission_authorizer.accessible_resource_ids(
            (resource_id,),
            scope,
        )
        if resource_id not in accessible:
            raise RagResourceSnapshotNotFoundError(resource_id)
