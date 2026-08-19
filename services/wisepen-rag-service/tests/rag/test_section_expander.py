import pytest
from common.utils.document import Section, SourceSpan

from rag.application.rag.navigate import SectionExpander
from rag.domain.models.acl import PermissionScope
from rag.domain.models.section_navigation import SectionDirection
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedSectionContent,
)


class _AllowAuthorizer:
    async def authorize_resource(self, *, resource_id, scope) -> bool:
        return True


class _Reader:
    def __init__(self) -> None:
        self.parent = _section("parent", "父标题", None, 0)
        self.children = [
            _section("child-1", "第一子标题", "parent", 0),
            _section("child-2", "第二子标题", "parent", 1),
        ]
        self.sections = {
            section.section_id: section for section in [self.parent, *self.children]
        }

    async def get_sections(self, resource_id, section_ids):
        result = {}
        for section_id in section_ids:
            section = self.sections.get(section_id)
            if section is None:
                continue
            siblings = (
                self.children
                if section.parent_section_id == "parent"
                else [self.parent]
            )
            index = siblings.index(section) if section in siblings else 0
            result[section_id] = PublishedSectionContent(
                section=section,
                text=f"{section.title}正文",
                parent=self.parent if section.parent_section_id else None,
                previous=siblings[index - 1] if index else None,
                next=siblings[index + 1] if index + 1 < len(siblings) else None,
                children=self.children if section_id == "parent" else [],
            )
        return result


def _section(
    section_id: str, title: str, parent_id: str | None, ordinal: int
) -> Section:
    return Section(
        section_id=section_id,
        title=title,
        level=2 if parent_id else 1,
        parent_section_id=parent_id,
        ordinal=ordinal,
        section_path=("父标题", title) if parent_id else (title,),
        own_span=SourceSpan(0, 10),
        subtree_span=SourceSpan(0, 10),
        content_spans=[SourceSpan(0, 10)],
    )


@pytest.mark.asyncio
async def test_children_expansion_uses_character_budget_and_cursor() -> None:
    reader = _Reader()
    expander = SectionExpander(reader=reader, authorizer=_AllowAuthorizer())
    scope = PermissionScope(user_id="user-1")

    first = await expander.expand(
        resource_id="resource-1",
        section_id="parent",
        direction=SectionDirection.CHILDREN,
        permission_scope=scope,
        char_budget=len("第一子标题正文"),
    )

    assert [child.section_id for child in first.sections] == ["child-1"]
    assert first.has_more is True
    assert first.next_after_section_id == "child-1"
    assert first.budget_exhausted is True

    second = await expander.expand(
        resource_id="resource-1",
        section_id="parent",
        direction=SectionDirection.CHILDREN,
        permission_scope=scope,
        char_budget=len("第一子标题正文"),
        after_section_id=first.next_after_section_id,
    )
    assert [child.section_id for child in second.sections] == ["child-2"]
    assert second.has_more is False


@pytest.mark.asyncio
async def test_parent_expansion_returns_readable_section_with_source_id() -> None:
    reader = _Reader()
    result = await SectionExpander(
        reader=reader,
        authorizer=_AllowAuthorizer(),
    ).expand(
        resource_id="resource-1",
        section_id="child-1",
        direction=SectionDirection.PARENT,
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert result.section.section_id == "parent"
    assert result.section.text == "父标题正文"
    assert result.section.allowed_directions == [SectionDirection.CHILDREN]
    assert result.from_section_id == "child-1"
