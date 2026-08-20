"""围绕 RAG 命中 Section 读取局部标题树元信息。"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from rag.application.rag.acl import PermissionAuthorizer
from rag.domain.models.acl import PermissionScope
from rag.domain.repositories.mongo import PublishedResourceReader
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)

from .content import ContentAccessRevokedError, ContentNotFoundError


@dataclass(slots=True)
class SectionMetadataView:
    """邻域导航使用的 Section 元信息，不包含正文。"""

    section_id: str
    title: str
    section_path: str
    has_children: bool
    page_range: str | None = None
    anchor_labels: list[str] = field(default_factory=list)
    is_current: bool | None = None


@dataclass(slots=True)
class SectionNeighborhoodView:
    """命中 Section 的直属父节点、兄弟窗口和直属孩子。"""

    parent: SectionMetadataView | None = None
    siblings: list[SectionMetadataView] = field(default_factory=list)
    children: list[SectionMetadataView] = field(default_factory=list)


class SectionNeighborhoodReader:
    """在当前发布 revision 中读取命中 Section 周围的标题树关系。"""

    __slots__ = ("_authorizer", "_reader")

    def __init__(
        self,
        *,
        reader: PublishedResourceReader,
        authorizer: PermissionAuthorizer,
    ) -> None:
        self._reader = reader
        self._authorizer = authorizer

    async def get_surrounding_outline(
        self,
        *,
        resource_id: str,
        section_id: str,
        window_size: int = 2,
        permission_scope: PermissionScope,
    ) -> SectionNeighborhoodView:
        if not 0 <= window_size <= 5:
            raise ValueError("window_size must be between 0 and 5")
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentNotFoundError(resource_id)

        current_map = await self._reader.get_sections(resource_id, [section_id])
        current = (current_map or {}).get(section_id)
        if current is None:
            raise ContentNotFoundError(section_id)

        parent = current.parent
        parent_content = None
        if parent is not None:
            parent_map = await self._reader.get_sections(
                resource_id,
                [parent.section_id],
            )
            parent_content = (parent_map or {}).get(parent.section_id)
            if parent_content is None:
                raise ContentNotFoundError(parent.section_id)

        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentAccessRevokedError(resource_id)

        siblings = _sibling_window(
            current=current,
            parent_content=parent_content,
            window_size=window_size,
        )
        related_ids = [
            section.section_id
            for section in (*siblings, *current.children)
        ]
        related_map = (
            await self._reader.get_sections(resource_id, related_ids)
            if related_ids
            else {}
        ) or {}
        related_map[section_id] = current
        return SectionNeighborhoodView(
            parent=_to_section_metadata(parent_content) if parent_content else None,
            siblings=[
                _to_section_metadata(
                    related_map[section.section_id],
                    is_current=True if section.section_id == section_id else None,
                )
                for section in siblings
                if section.section_id in related_map
            ],
            children=[
                _to_section_metadata(related_map[section.section_id])
                for section in current.children
                if section.section_id in related_map
            ],
        )


def _sibling_window(
    *,
    current: PublishedSectionContent,
    parent_content: PublishedSectionContent | None,
    window_size: int,
) -> Sequence:
    if parent_content is None:
        return [current.section]
    if window_size == 0:
        return [current.section]
    siblings = parent_content.children
    current_index = next(
        index
        for index, section in enumerate(siblings)
        if section.section_id == current.section.section_id
    )
    start = max(0, current_index - window_size)
    end = min(len(siblings), current_index + window_size + 1)
    return [
        section
        for index, section in enumerate(siblings[start:end], start=start)
        if index <= current_index + window_size
    ]


def _to_section_metadata(
    content: PublishedSectionContent,
    *,
    is_current: bool | None = None,
) -> SectionMetadataView:
    return SectionMetadataView(
        section_id=content.section.section_id,
        title=content.section.title,
        section_path=" > ".join(content.section.section_path),
        has_children=bool(content.children),
        page_range=_format_page_range(content.page_labels),
        anchor_labels=list(content.anchor_labels),
        is_current=is_current,
    )


def _format_page_range(page_labels: list[str]) -> str | None:
    labels = list(dict.fromkeys(page_labels))
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} - {labels[-1]}"
