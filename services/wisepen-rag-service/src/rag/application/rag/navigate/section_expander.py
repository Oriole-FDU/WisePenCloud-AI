"""沿标题树执行单步、可预算的方向导航。"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.read.content import (
    ContentAccessRevokedError,
    ContentNotFoundError,
    SectionContentView,
    to_section_content_view,
)
from rag.domain.models.acl import PermissionScope
from rag.domain.models.section_navigation import SectionDirection
from rag.domain.repositories.mongo import PublishedResourceReader
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)


@dataclass(slots=True)
class SectionExpandResult:
    """parent/previous/next 返回的单个可读 Section。"""

    from_section_id: str
    section: SectionContentView


@dataclass(slots=True)
class SectionChildrenExpandResult:
    """children 专用结果；分页和预算字段不污染单 Section 导航。"""

    from_section_id: str
    sections: list[SectionContentView] = field(default_factory=list)
    has_more: bool = False
    next_after_section_id: str | None = None
    budget_exhausted: bool = False


class SectionExpander:
    """展开一个方向，并返回目标 Section 的直属正文视图。"""

    __slots__ = ("_authorizer", "_reader")

    def __init__(
        self, *, reader: PublishedResourceReader, authorizer: PermissionAuthorizer
    ) -> None:
        self._reader = reader
        self._authorizer = authorizer

    async def expand(
        self,
        *,
        resource_id: str,
        section_id: str,
        direction: SectionDirection,
        permission_scope: PermissionScope,
        char_budget: int = 12000,
        after_section_id: str | None = None,
    ) -> SectionExpandResult | SectionChildrenExpandResult:
        if char_budget < 1:
            raise ValueError("char_budget must be positive")
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id, scope=permission_scope
        ):
            raise ContentNotFoundError(resource_id)

        current_map = await self._reader.get_sections(resource_id, [section_id])
        current = (current_map or {}).get(section_id)
        if current is None:
            raise ContentNotFoundError(section_id)

        related_ids = _related_ids(current, direction)
        related_map = (
            await self._reader.get_sections(resource_id, related_ids)
            if related_ids
            else {}
        )
        if not await self._authorizer.authorize_resource(
            resource_id=resource_id, scope=permission_scope
        ):
            raise ContentAccessRevokedError(resource_id)

        if direction is SectionDirection.CHILDREN:
            return self._expand_children(
                resource_id=resource_id,
                section_id=section_id,
                current=current,
                related_map=related_map or {},
                char_budget=char_budget,
                after_section_id=after_section_id,
            )
        target = _direction_target(current, direction)
        if target is None:
            raise ContentNotFoundError(section_id)
        target_content = (related_map or {}).get(target.section_id)
        if target_content is None:
            raise ContentNotFoundError(target.section_id)
        return SectionExpandResult(
            from_section_id=section_id,
            section=to_section_content_view(target_content),
        )

    async def parent(self, **kwargs) -> SectionExpandResult:
        return await self.expand(direction=SectionDirection.PARENT, **kwargs)

    async def children(self, **kwargs) -> SectionExpandResult:
        return await self.expand(direction=SectionDirection.CHILDREN, **kwargs)

    async def previous(self, **kwargs) -> SectionExpandResult:
        return await self.expand(direction=SectionDirection.PREVIOUS, **kwargs)

    async def next(self, **kwargs) -> SectionExpandResult:
        return await self.expand(direction=SectionDirection.NEXT, **kwargs)

    @staticmethod
    def _expand_children(
        *,
        resource_id,
        section_id,
        current,
        related_map,
        char_budget,
        after_section_id,
    ) -> SectionChildrenExpandResult:
        children = current.children
        start = 0
        if after_section_id is not None:
            ids = [child.section_id for child in children]
            if after_section_id not in ids:
                raise ValueError("after_section_id is not a child of section_id")
            start = ids.index(after_section_id) + 1
        selected = []
        used = 0
        budget_exhausted = False
        for child in children[start:]:
            content = related_map.get(child.section_id)
            if content is None:
                continue
            section = to_section_content_view(content)
            # children 预算约束的是实际返回的直属正文字符数。
            cost = len(section.text)
            if selected and used + cost > char_budget:
                budget_exhausted = True
                break
            selected.append(section)
            used += cost
            if used > char_budget:
                budget_exhausted = True
                break
        has_more = start + len(selected) < len(children)
        return SectionChildrenExpandResult(
            from_section_id=section_id,
            sections=selected,
            has_more=has_more,
            next_after_section_id=selected[-1].section_id
            if has_more and selected
            else None,
            budget_exhausted=budget_exhausted,
        )


def _related_ids(
    content: PublishedSectionContent, direction: SectionDirection
) -> Sequence[str]:
    if direction is SectionDirection.CHILDREN:
        return [child.section_id for child in content.children]
    target = _direction_target(content, direction)
    return [target.section_id] if target is not None else []


def _direction_target(content: PublishedSectionContent, direction: SectionDirection):
    return {
        SectionDirection.PARENT: content.parent,
        SectionDirection.PREVIOUS: content.previous,
        SectionDirection.NEXT: content.next,
    }.get(direction)
