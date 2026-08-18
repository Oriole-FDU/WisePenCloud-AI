"""从当前已发布权威源按页或 Section 确定性读取正文。"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from rag.application.rag.acl import PermissionAuthorizer
from rag.domain.models.acl import PermissionScope
from rag.domain.models.structure import Section
from rag.domain.repositories.mongo import PublishedResourceReader
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)


class ContentNotFoundError(RuntimeError):
    """资源没有可读取的发布 revision。"""


class ContentAccessRevokedError(RuntimeError):
    """读取期间资源失去可读权限。"""


@dataclass(slots=True)
class SectionAnchorView:
    """READ 中的轻量 Section 锚点；flat text 由 synthetic Section 提供入口。"""

    section_id: str
    title: str
    section_path: str
    preview: str | None = None


@dataclass(slots=True)
class SectionNavigationView:
    """Section 的轻量导航入口。"""

    parent: SectionAnchorView | None = None
    previous: SectionAnchorView | None = None
    next: SectionAnchorView | None = None
    children: list[SectionAnchorView] = field(default_factory=list)


@dataclass(slots=True)
class SectionContentView:
    """Section 直属正文及导航；flat text 使用 synthetic Section 保持可读。"""

    title: str
    section_path: str
    # include_body=False 时为 None，配合端点 exclude_none 从响应中彻底省略。
    text: str | None = None
    navigation: SectionNavigationView = field(default_factory=SectionNavigationView)


class DocumentContentReader:
    """读取当前发布 revision，并只向上层返回模型可读的语义视图。"""

    __slots__ = ("_authorizer", "_reader")

    def __init__(
        self,
        *,
        reader: PublishedResourceReader,
        authorizer: PermissionAuthorizer,
    ) -> None:
        self._reader = reader
        self._authorizer = authorizer

    async def get_pages(
        self,
        *,
        resource_id: str,
        page_labels: Sequence[str],
        permission_scope: PermissionScope,
    ) -> dict[str, str]:
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentNotFoundError(resource_id)
        pages = await self._reader.get_pages(resource_id, page_labels)
        if pages is None:
            raise ContentNotFoundError(resource_id)
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentAccessRevokedError(resource_id)
        return pages

    async def get_sections(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
        permission_scope: PermissionScope,
        include_body: bool = True,
        exclude_directions: Sequence[str] = (),
    ) -> dict[str, SectionContentView]:
        """读取 Section 视图；include_body=False 裁剪正文，exclude_directions 过滤导航方向。"""
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentNotFoundError(resource_id)
        sections = await self._reader.get_sections(resource_id, section_ids)
        if sections is None:
            raise ContentNotFoundError(resource_id)
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id,
            scope=permission_scope,
        ):
            raise ContentAccessRevokedError(resource_id)
        # 宽松黑名单：只取有效方向名做过滤，未知输入静默忽略。
        excluded = frozenset(exclude_directions) & frozenset(
            ("parent", "previous", "next", "children")
        )
        return {
            section_id: _to_section_content_view(
                content,
                include_body=include_body,
                excluded_directions=excluded,
            )
            for section_id, content in sections.items()
        }


def format_page_range(page_labels: Sequence[str]) -> str | None:
    """把内部有序 page labels 投影为统一的模型可见页范围。"""
    labels = list(dict.fromkeys(page_labels))
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} - {labels[-1]}"


def _to_section_content_view(
    content: PublishedSectionContent,
    *,
    include_body: bool = True,
    excluded_directions: frozenset[str] = frozenset(),
) -> SectionContentView:
    return SectionContentView(
        title=content.section.title,
        section_path=" > ".join(content.section.section_path),
        text=content.text if include_body else None,
        navigation=SectionNavigationView(
            parent=None
            if "parent" in excluded_directions
            else _optional_section_anchor_view(content.parent),
            previous=None
            if "previous" in excluded_directions
            else _optional_section_anchor_view(content.previous),
            next=None
            if "next" in excluded_directions
            else _optional_section_anchor_view(content.next),
            children=[]
            if "children" in excluded_directions
            else [_to_section_anchor_view(child) for child in content.children],
        ),
    )


def _to_section_anchor_view(section: Section) -> SectionAnchorView:
    return SectionAnchorView(
        section_id=section.section_id,
        title=section.title,
        section_path=" > ".join(section.section_path),
        preview=section.preview if section.preview else None,
    )


def _optional_section_anchor_view(section: Section | None) -> SectionAnchorView | None:
    return _to_section_anchor_view(section) if section is not None else None
