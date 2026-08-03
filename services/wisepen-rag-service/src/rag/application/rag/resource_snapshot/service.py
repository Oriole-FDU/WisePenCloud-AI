from __future__ import annotations

from dataclasses import dataclass

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.application.rag.repositories import RagResourceSnapshotRepository
from rag.application.rag.retrieval import RagPermissionScope

from .models import RagResourceContentReadResult, RagResourceSnapshot


class RagResourceSnapshotNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RagResourceContentRequest:
    resource_id: str
    locator_name: str | None = None
    start: int | None = None
    end: int | None = None


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

    async def read(
        self,
        *,
        request: RagResourceContentRequest,
        scope: RagPermissionScope,
    ) -> RagResourceContentReadResult:
        await self._ensure_access(request.resource_id, scope=scope)
        result = await self._repository.read_applied_resource_content(
            resource_id=request.resource_id,
            locator_name=request.locator_name,
            start=request.start,
            end=request.end,
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
