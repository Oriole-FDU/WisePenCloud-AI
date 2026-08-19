import pytest
from common.core.domain import GroupRoleType
from common.security import SecurityContextHolder
from common.utils.document import (
    Anchor,
    OutlineAssembler,
    OutlineNode,
    Page,
    Section,
    SourceSpan,
)
from pydantic import TypeAdapter, ValidationError

from rag.api.endpoints.read import (
    get_document_outline,
    read_pages,
)
from rag.api.router import api_router
from rag.api.schemas import (
    DocumentOutlineRequest,
    ReadPagesRequest,
    ReadSectionsRequest,
    ResourceRequest,
)
from rag.application.rag.read.content import (
    DocumentContentReader,
    SectionContentView,
)
from rag.application.rag.read.outline import (
    DocumentOutlineReader,
    DocumentOutlineResult,
)
from rag.domain.models.acl import PermissionScope
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedDocumentOutline,
    PublishedSectionContent,
)


class _AllowAuthorizer:
    async def authorize_resource(self, *, resource_id, scope) -> bool:
        return True


class _PublishedResourceReader:
    async def get_pages(self, resource_id, page_labels):
        return {"1": "<!-- page 1 -->\n正文"}

    async def get_sections(self, resource_id, section_ids):
        return {
            "section-1": PublishedSectionContent(
                section=_section(),
                text="正文",
                children=[_child_section()],
            )
        }


class _ContentReader:
    async def read_pages(self, **kwargs):
        return {"1": "正文"}

    async def read_sections(self, **kwargs):
        return {
            "section-1": SectionContentView(
                section_id="section-1",
                title="标题",
                section_path="标题",
                text="正文",
                allowed_directions=[],
            )
        }


class _OutlineReader:
    def __init__(self) -> None:
        self.scope = None

    async def get_document_outline(
        self, *, resource_id, permission_scope, root_section_id=None, depth=None
    ):
        self.scope = permission_scope
        return DocumentOutlineResult(
            resource_id=resource_id,
            content_revision="revision-1",
            document_version=3,
            total_length=12,
            outline=[
                OutlineNode(
                    section_id="section-1",
                    title="标题",
                    length=12,
                    page_range="1",
                )
            ],
        )


def _section() -> Section:
    return Section(
        section_id="section-1",
        title="标题",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=("标题",),
        own_span=SourceSpan(0, 12),
        subtree_span=SourceSpan(0, 12),
        content_spans=[SourceSpan(4, 12)],
        preview="正文",
    )


def _child_section() -> Section:
    return Section(
        section_id="section-2",
        title="子标题",
        level=2,
        parent_section_id="section-1",
        ordinal=0,
        section_path=("标题", "子标题"),
        own_span=SourceSpan(6, 12),
        subtree_span=SourceSpan(6, 12),
        content_spans=[SourceSpan(9, 12)],
        preview="子正文",
    )


def _to_outline(
    sections: list[Section],
    pages: list[Page],
    anchors: list[Anchor],
) -> list[OutlineNode]:
    return OutlineAssembler().assemble(
        sections=sections,
        pages=pages,
        anchors=anchors,
    )


def test_read_request_schemas_and_routes() -> None:
    with pytest.raises(ValidationError):
        ResourceRequest(resource_id="resource-1", extra_field=True)
    with pytest.raises(ValidationError):
        ReadPagesRequest(
            resource_id="resource-1",
            page_labels=[str(index) for index in range(21)],
        )
    with pytest.raises(ValidationError):
        ReadSectionsRequest(resource_id="resource-1", section_ids=[])

    paths = {route.path for route in api_router.routes}
    assert "/getDocumentOutline" in paths
    assert "/readPages" in paths
    assert "/readSections" in paths
    assert "/expandSection" in paths
    assert "/getPageContent" not in paths
    assert "/getSectionContent" not in paths
    assert "/expandDiscoveredSections" not in paths


@pytest.mark.asyncio
async def test_outline_keeps_title_and_removes_level() -> None:
    reader = _OutlineReader()
    SecurityContextHolder.set_group_role_map('{"group-1": 1}')

    response = await get_document_outline(
        DocumentOutlineRequest(resource_id="resource-1"),
        user_id="user-1",
        reader=reader,
    )

    node = response.data.outline[0]
    assert node.title == "标题"
    assert node.length == 12
    assert node.page_range == "1"
    assert not hasattr(node, "level")
    assert not hasattr(node, "section_path")
    assert reader.scope.group_roles == {"group-1": GroupRoleType.ADMIN}


@pytest.mark.asyncio
async def test_page_view_returns_text_only() -> None:
    response = await read_pages(
        ReadPagesRequest(resource_id="resource-1", page_labels=["1"]),
        user_id="user-1",
        reader=_ContentReader(),
    )

    # 页标签是请求参数，正文已含锚点；页视图直接返回文本。
    assert response.data["1"] == "正文"


@pytest.mark.asyncio
async def test_section_read_returns_authoritative_text_without_blocks() -> None:
    reader = DocumentContentReader(
        reader=_PublishedResourceReader(),
        authorizer=_AllowAuthorizer(),
    )
    sections = await reader.read_sections(
        resource_id="resource-1",
        section_ids=["section-1"],
        permission_scope=PermissionScope(user_id="user-1"),
    )

    view = sections["section-1"]
    payload = TypeAdapter(SectionContentView).dump_python(
        view,
        mode="json",
        exclude_none=True,
    )
    assert view.title == "标题"
    assert view.text == "正文"
    assert view.allowed_directions == ["children"]
    # 页码与锚点信息已从 Section 视图移除（正文可见、目录可查）。
    assert not hasattr(view, "page_range")
    assert not hasattr(view, "anchor_labels")
    assert "page_range" not in payload
    assert "anchor_labels" not in payload
    assert payload["section_id"] == "section-1"
    assert "section" not in payload
    assert "reading_blocks" not in payload
    assert payload["allowed_directions"] == ["children"]


@pytest.mark.asyncio
async def test_flat_text_read_keeps_synthetic_section_context() -> None:
    reader = DocumentContentReader(
        reader=_FlatPublishedResourceReader(),
        authorizer=_AllowAuthorizer(),
    )
    pages = await reader.read_pages(
        resource_id="flat-resource",
        page_labels=["1"],
        permission_scope=PermissionScope(user_id="user-1"),
    )
    sections = await reader.read_sections(
        resource_id="flat-resource",
        section_ids=["flat-section"],
        permission_scope=PermissionScope(user_id="user-1"),
    )

    page_payload = pages["1"]
    section_payload = TypeAdapter(SectionContentView).dump_python(
        sections["flat-section"], mode="json", exclude_none=True
    )
    assert page_payload == "平铺正文"
    assert section_payload == {
        "section_id": "flat-section",
        "title": "全文片段 1",
        "section_path": "全文片段 1",
        "text": "平铺正文",
        "allowed_directions": [],
    }


def test_outline_uses_human_page_range() -> None:
    outline = _to_outline(
        [_section(), _child_section()],
        [
            Page(0, "1", SourceSpan(0, 6)),
            Page(1, "3", SourceSpan(6, 12)),
        ],
        [],
    )

    assert outline[0].page_range == "1 - 3"
    assert outline[0].length == 12
    assert outline[0].children[0].page_range == "3"
    assert outline[0].children[0].length == 6

    flat_outline = _to_outline([_flat_section()], [], [])
    assert flat_outline[0].page_range is None
    assert flat_outline[0].title == "全文片段 1"
    assert flat_outline[0].length == 4
    assert flat_outline[0].children == []


@pytest.mark.asyncio
async def test_outline_supports_root_and_depth_projection() -> None:
    parent = OutlineNode(
        section_id="parent",
        title="父标题",
        length=10,
        children=[OutlineNode(section_id="child", title="子标题", length=4)],
    )

    class _StructureReader:
        async def get_document_outline(self, resource_id):
            return PublishedDocumentOutline(
                resource_id=resource_id,
                content_revision="revision-1",
                document_version=1,
                total_length=10,
                outline=[parent],
            )

    reader = DocumentOutlineReader(
        structure_reader=_StructureReader(),
        authorizer=_AllowAuthorizer(),
    )
    result = await reader.get_document_outline(
        resource_id="resource-1",
        permission_scope=PermissionScope(user_id="user-1"),
        root_section_id="parent",
        depth=0,
    )

    assert [node.section_id for node in result.outline] == ["parent"]
    assert result.outline[0].children == []
    assert result.outline[0].children_truncated is True


def test_outline_nodes_carry_anchor_labels() -> None:
    # 锚点只归属直属 own_span 正文（父节点不冒泡展示子树锚点），页码仍按子树统计。
    parent = Section(
        section_id="section-1",
        title="标题",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["标题"],
        own_span=SourceSpan(0, 6),
        subtree_span=SourceSpan(0, 12),
        content_spans=[SourceSpan(0, 6)],
        preview="正文",
    )
    child = Section(
        section_id="section-2",
        title="子标题",
        level=2,
        parent_section_id="section-1",
        ordinal=0,
        section_path=["标题", "子标题"],
        own_span=SourceSpan(6, 12),
        subtree_span=SourceSpan(6, 12),
        content_spans=[SourceSpan(9, 12)],
        preview="子正文",
    )
    outline = _to_outline(
        [parent, child],
        [],
        [
            Anchor("Table 1", SourceSpan(4, 6)),
            Anchor("Figure 2", SourceSpan(9, 11)),
        ],
    )

    # Table 1 落在父节点直属 own_span (0,6)；Figure 2 落在子节点直属 own_span，不向父节点冒泡。
    assert outline[0].anchor_labels == ["Table 1"]
    assert outline[0].children[0].anchor_labels == ["Figure 2"]


def test_outline_exposes_titled_root_as_preamble_entry() -> None:
    # 带合成标题的虚拟根（第一个标题之前存在前言正文）应作为叶子节点置顶，
    # 其子标题平级排在其后，且页范围按 own_span 而非覆盖全文的 subtree_span 计算。
    root = Section(
        section_id="root-section",
        title="文档开头",
        level=0,
        parent_section_id=None,
        ordinal=0,
        section_path=["文档开头"],
        own_span=SourceSpan(0, 4),
        subtree_span=SourceSpan(0, 12),
        content_spans=[SourceSpan(0, 4)],
        preview="前言",
    )
    heading = Section(
        section_id="section-1",
        title="标题",
        level=1,
        parent_section_id="root-section",
        ordinal=0,
        section_path=["标题"],
        own_span=SourceSpan(4, 12),
        subtree_span=SourceSpan(4, 12),
        content_spans=[SourceSpan(4, 12)],
        preview="正文",
    )

    outline = _to_outline(
        [root, heading],
        [
            Page(0, "1", SourceSpan(0, 6)),
            Page(1, "3", SourceSpan(6, 12)),
        ],
        [],
    )

    assert outline[0].title == "文档开头"
    assert outline[0].page_range == "1"
    assert outline[0].length == 4
    assert outline[0].children == []
    assert outline[1].title == "标题"
    assert outline[1].page_range == "1 - 3"
    assert outline[1].length == 8
    assert outline[1].children == []


def test_outline_skips_nameless_root_without_preamble() -> None:
    # 无前言的无名 root 仍被隐藏，大纲直接从其子标题展开（维持既有行为）。
    root = Section(
        section_id="root-section",
        title="",
        level=0,
        parent_section_id=None,
        ordinal=0,
        section_path=[],
        own_span=SourceSpan(0, 0),
        subtree_span=SourceSpan(0, 12),
        content_spans=[],
        preview="",
    )
    heading = Section(
        section_id="section-1",
        title="标题",
        level=1,
        parent_section_id="root-section",
        ordinal=0,
        section_path=["标题"],
        own_span=SourceSpan(0, 12),
        subtree_span=SourceSpan(0, 12),
        content_spans=[SourceSpan(4, 12)],
        preview="正文",
    )

    outline = _to_outline([root, heading], [], [])

    assert [node.title for node in outline] == ["标题"]
    assert outline[0].children == []


@pytest.mark.asyncio
async def test_section_read_includes_body_and_exposes_allowed_directions() -> None:
    reader = DocumentContentReader(
        reader=_NavPublishedResourceReader(),
        authorizer=_AllowAuthorizer(),
    )
    sections = await reader.read_sections(
        resource_id="resource-1",
        section_ids=["section-1"],
        permission_scope=PermissionScope(user_id="user-1"),
    )

    view = sections["section-1"]
    payload = TypeAdapter(SectionContentView).dump_python(
        view,
        mode="json",
        exclude_none=True,
    )
    assert view.text == "正文"
    assert view.allowed_directions == ["parent", "children", "previous", "next"]
    assert payload["allowed_directions"] == ["parent", "children", "previous", "next"]


def test_section_read_request_rejects_navigation_controls() -> None:
    with pytest.raises(ValidationError):
        ReadSectionsRequest(
            resource_id="resource-1",
            section_ids=["section-1"],
            include_body=False,
        )
    with pytest.raises(ValidationError):
        ReadSectionsRequest(
            resource_id="resource-1",
            section_ids=["section-1"],
            exclude_directions=["previous"],
        )


class _NavPublishedResourceReader:
    """提供带完整导航事实（父/前/后/子）的 Section 内容。"""

    async def get_sections(self, resource_id, section_ids):
        return {
            "section-1": PublishedSectionContent(
                section=_section(),
                text="正文",
                parent=_nav_section("section-parent", "父标题"),
                previous=_nav_section("section-prev", "上一节"),
                next=_nav_section("section-next", "下一节"),
                children=[_child_section()],
            )
        }


def _nav_section(section_id: str, title: str) -> Section:
    return Section(
        section_id=section_id,
        title=title,
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=[title],
        own_span=SourceSpan(0, 4),
        subtree_span=SourceSpan(0, 4),
        content_spans=[SourceSpan(0, 4)],
    )


class _FlatPublishedResourceReader:
    async def get_pages(self, resource_id, page_labels):
        return {"1": "平铺正文"}

    async def get_sections(self, resource_id, section_ids):
        return {
            "flat-section": PublishedSectionContent(
                section=_flat_section(),
                text="平铺正文",
            )
        }


def _flat_section() -> Section:
    return Section(
        section_id="flat-section",
        title="全文片段 1",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["全文片段 1"],
        own_span=SourceSpan(0, 4),
        subtree_span=SourceSpan(0, 4),
        content_spans=[SourceSpan(0, 4)],
    )
