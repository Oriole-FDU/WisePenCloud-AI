"""读取已发布 revision 中随资源更新写入的精简目录。"""

from dataclasses import dataclass, field, replace

from common.utils.document import OutlineNode

from rag.application.rag.acl import PermissionAuthorizer
from rag.domain.models.acl import PermissionScope
from rag.domain.repositories.mongo import PublishedResourceReader

from .content import ContentAccessRevokedError, ContentNotFoundError


@dataclass(slots=True)
class DocumentOutlineResult:
    """READ outline 面向 API 的结果。"""

    resource_id: str
    content_revision: str
    document_version: int
    total_length: int
    outline: list[OutlineNode] = field(default_factory=list)


class DocumentOutlineReader:
    """执行权限校验，并读取 revision 已持久化的目录。"""

    __slots__ = ("_authorizer", "_structure_reader")

    def __init__(
        self,
        *,
        structure_reader: PublishedResourceReader,
        authorizer: PermissionAuthorizer,
    ) -> None:
        self._structure_reader = structure_reader
        self._authorizer = authorizer

    async def get_document_outline(
        self,
        *,
        resource_id: str,
        permission_scope: PermissionScope,
        root_section_id: str | None = None,
        depth: int | None = None,
    ) -> DocumentOutlineResult:
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentNotFoundError(resource_id)

        outline = await self._structure_reader.get_document_outline(resource_id)
        if outline is None:
            raise ContentNotFoundError(resource_id)

        # 目录读取跨越权限检查和 Mongo 查询，返回前再次确认资源仍可读。
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentAccessRevokedError(resource_id)

        projected = _project_outline(
            outline.outline, root_section_id=root_section_id, depth=depth
        )
        return DocumentOutlineResult(
            resource_id=outline.resource_id,
            content_revision=outline.content_revision,
            document_version=outline.document_version,
            total_length=outline.total_length,
            outline=projected,
        )


def _project_outline(
    nodes: list[OutlineNode],
    *,
    root_section_id: str | None,
    depth: int | None,
) -> list[OutlineNode]:
    if depth is not None and depth < 0:
        raise ValueError("depth must be non-negative")
    selected = nodes
    if root_section_id is not None:
        selected = [_find_node(nodes, root_section_id)]

    def project(node: OutlineNode, remaining: int | None) -> OutlineNode:
        if remaining is not None and remaining == 0:
            return replace(
                node,
                children=[],
                children_truncated=True if node.children else None,
            )
        next_remaining = None if remaining is None else remaining - 1
        children = [project(child, next_remaining) for child in node.children]
        return replace(node, children=children, children_truncated=None)

    return [project(node, depth) for node in selected]


def _find_node(nodes: list[OutlineNode], section_id: str) -> OutlineNode:
    for node in nodes:
        if node.section_id == section_id:
            return node
        found = _find_node_or_none(node.children, section_id)
        if found is not None:
            return found
    raise ContentNotFoundError(section_id)


def _find_node_or_none(nodes: list[OutlineNode], section_id: str) -> OutlineNode | None:
    for node in nodes:
        if node.section_id == section_id:
            return node
        found = _find_node_or_none(node.children, section_id)
        if found is not None:
            return found
    return None
