"""从当前已发布权威源按页或 Section 确定性读取正文。"""

from collections.abc import Sequence
from dataclasses import dataclass

from rag.application.rag.acl import PermissionAuthorizer
from rag.domain.models.acl import PermissionScope
from rag.domain.models.section_navigation import SectionDirection
from rag.domain.repositories.mongo import PublishedResourceReader
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)


class ContentNotFoundError(RuntimeError):
    """资源没有可读取的发布 revision。"""


class ContentAccessRevokedError(RuntimeError):
    """读取期间资源失去可读权限。"""


@dataclass(slots=True)
class SectionContentView:
    """Section 直属正文；方向导航通过 SectionExpander 单独负责。"""

    section_id: str
    title: str
    section_path: str
    text: str
    allowed_directions: list[SectionDirection]


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

    async def read_pages(
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

    async def read_sections(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
        permission_scope: PermissionScope,
    ) -> dict[str, SectionContentView]:
        """读取 Section 正文；方向导航由 SectionExpander 单独负责。"""
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
        return {
            section_id: to_section_content_view(content)
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


def to_section_content_view(
    content: PublishedSectionContent,
) -> SectionContentView:
    allowed_directions: list[SectionDirection] = []
    if content.parent is not None:
        allowed_directions.append(SectionDirection.PARENT)
    if content.children:
        allowed_directions.append(SectionDirection.CHILDREN)
    if content.previous is not None:
        allowed_directions.append(SectionDirection.PREVIOUS)
    if content.next is not None:
        allowed_directions.append(SectionDirection.NEXT)
    return SectionContentView(
        section_id=content.section.section_id,
        title=content.section.title,
        section_path=" > ".join(content.section.section_path),
        text=content.text,
        allowed_directions=allowed_directions,
    )
