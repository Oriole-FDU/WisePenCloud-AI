"""资源副本结构发现与正文读取能力。"""

from __future__ import annotations

from dataclasses import dataclass, field

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.domain.repositories import RagResourceSnapshotRepository
from rag.application.rag.retrieval import RagPermissionScope
from rag.utils.chunkers import SourceSpan


@dataclass(frozen=True, slots=True)
class RagResourceSnapshot:
    """资源的解析后文档结构。"""

    resource_id: str
    document_version: int
    content_revision: str
    total_length: int
    pages: tuple["RagResourceSnapshotPage", ...] = ()
    sections: tuple["RagResourceSnapshotSection", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceSnapshotPage:
    """可按页读取的结构入口。"""

    page_label: str


@dataclass(frozen=True, slots=True)
class RagResourceSnapshotSection:
    """可按 Section 读取的结构树节点。"""

    section_id: str
    title: str
    level: int
    section_path: tuple[str, ...]
    has_content: bool
    children: tuple["RagResourceSnapshotSection", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceContentWindow:
    """从资源副本读取出的窗口。"""

    text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...] = ()
    page_labels: tuple[str, ...] = ()
    section_paths: tuple[tuple[str, ...], ...] = ()
    anchor_labels: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RagResourceContentReadResult:
    """资源副本读取结果。"""

    resource_id: str
    content_revision: str | None = None
    document_version: int | None = None
    items: tuple["RagResourceContentItem", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceContentItem:
    """一次批量读取中的单个 page/section 结果。"""

    key: str
    kind: str
    reason: str | None = None
    windows: tuple[RagResourceContentWindow, ...] = ()


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
            # 对无权限和不存在统一返回 not found，避免暴露资源是否存在。
            raise RagResourceSnapshotNotFoundError(resource_id)
