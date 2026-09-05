"""把 active Document 的 Section 事实渲染成导航 Markdown。"""

from collections import defaultdict
from dataclasses import dataclass

from common.utils.document import Page, Section, SourceSpan

from rag.application.reading import DocumentReadError
from rag.application.snapshot import ActiveDocumentSnapshotLoader
from rag.domain.acl import PermissionScope


@dataclass(frozen=True, slots=True)
class NeighborhoodItem:
    """单个 section 的邻域大纲信息。"""

    resource_id: str
    section_id: str
    section_path: str
    outline: str


class OutlineBuilder:
    """提供独立邻域窗口和有限深度全局大纲，不合并多个窗口。"""

    def __init__(self, *, snapshots: ActiveDocumentSnapshotLoader) -> None:
        self._snapshots = snapshots

    async def neighborhood(
        self,
        section_ids: list[str],
        *,
        sibling_steps: int = 1,
        scope: PermissionScope,
    ) -> list[NeighborhoodItem]:
        """为每个 section 生成邻域大纲：显示父级、同级及一级子级。"""
        locations = await self._snapshots.locate_sections(section_ids, scope=scope)

        items: list[NeighborhoodItem] = []
        for section_id in section_ids:
            location = locations.get(section_id)
            if location is None:
                # 不存在、旧 revision 和无权 Section 对外必须不可区分。
                raise DocumentReadError("section is not visible")

            structure = location.document.structure

            # 获取同一父级下的所有兄弟节点
            siblings = sorted(
                (
                    section
                    for section in structure.sections
                    if section.parent_section_id == location.section.parent_section_id
                ),
                key=lambda s: s.ordinal,
            )
            index = siblings.index(location.section)

            # 查找父节点（如果存在）
            parent = next(
                (
                    section
                    for section in structure.sections
                    if section.section_id == location.section.parent_section_id
                ),
                None,
            )

            # 取邻域窗口内的兄弟节点
            start = max(0, index - sibling_steps)
            end = index + sibling_steps + 1
            visible_siblings = siblings[start:end]

            # 构建父子关系映射
            children_by_parent = _children_by_parent(structure.sections)

            lines: list[str] = []

            # 1. 父节点行（若存在）
            if parent is not None:
                lines.append(
                    _node_line(
                        structure,
                        parent,
                        indent=0,
                        children_by_parent=children_by_parent,
                    )
                )

            # 2. 可见兄弟节点（含当前节点标记）
            for sibling in visible_siblings:
                is_current = sibling.section_id == section_id
                indent = 1 if parent is not None else 0
                lines.append(
                    _node_line(
                        structure,
                        sibling,
                        indent=indent,
                        current=is_current,
                        children_by_parent=children_by_parent,
                    )
                )
                # 3. 若为当前节点，展开其直接子节点（缩进+1）
                if is_current:
                    child_indent = 2 if parent is not None else 1
                    for child in children_by_parent.get(section_id, []):
                        lines.append(
                            _node_line(
                                structure,
                                child,
                                indent=child_indent,
                                children_by_parent=children_by_parent,
                            )
                        )

            items.append(
                _item(
                    location.document,
                    location.section,
                    section_id,
                    "\n".join(lines),
                )
            )

        return items

    async def global_outline(
        self,
        resource_id: str,
        *,
        max_level: int = 2,
        scope: PermissionScope,
    ) -> str:
        """生成整个文档的全局大纲，限制最大层级。"""
        documents = await self._snapshots.load_documents([resource_id], scope=scope)
        document = documents.get(resource_id)
        if document is None:
            raise DocumentReadError("document is not visible")

        children_by_parent = _children_by_parent(document.structure.sections)
        lines: list[str] = []

        def visit(section: Section, indent: int) -> None:
            # 当 max_level 为 0 时，表现为不限制层级，显示所有层级
            if max_level > 0 and section.level > max_level:
                return
            lines.append(
                _node_line(
                    document.structure,
                    section,
                    indent=indent,
                    children_by_parent=children_by_parent,
                )
            )
            for child in children_by_parent.get(section.section_id, []):
                visit(child, indent + 1)

        # 从根节点（parent_section_id 为 None）开始遍历
        for root in children_by_parent.get(None, []):
            visit(root, 0)

        return "\n".join(lines)


def _children_by_parent(sections: list[Section]) -> dict[str | None, list[Section]]:
    """按 parent_section_id 分组，并对每组按 ordinal 排序。"""
    result: dict[str | None, list[Section]] = defaultdict(list)
    for section in sections:
        result[section.parent_section_id].append(section)
    for children in result.values():
        children.sort(key=lambda s: s.ordinal)
    return result


def _item(
    document,
    section: Section,
    section_id: str,
    outline: str,
) -> NeighborhoodItem:
    """从文档和 section 构造 NeighborhoodItem。"""
    return NeighborhoodItem(
        resource_id=document.resource_id,
        section_id=section_id,
        section_path=" > ".join(section.section_path),
        outline=outline,
    )


def _node_line(
    structure,
    section: Section,
    *,
    indent: int,
    current: bool = False,
    children_by_parent=None,
) -> str:
    """生成大纲中的单行 Markdown 条目。

    格式：缩进 + "- title [标记] [+子节点数] (字符数, 页码) [锚点]"
    """
    children = (children_by_parent or {}).get(section.section_id, [])
    suffix = f" [+{len(children)}]" if children else ""
    marker = " [C]" if current else f" {{#{section.section_id}}}"

    page_range = _page_range(structure.pages, section.subtree_span)
    char_count = sum(span.length for span in section.content_spans)
    metadata = f" ({char_count} chars"
    if page_range:
        metadata += f", p.{page_range}"
    metadata += ")"

    anchors = [
        anchor.label
        for anchor in structure.anchors
        if _overlaps(anchor.source_span, section.own_span)
    ]
    if anchors:
        metadata += " [" + ", ".join(anchors) + "]"

    return "  " * indent + f"- {section.title.strip()}{marker}{suffix}{metadata}"


def _page_range(pages: list[Page], span: SourceSpan) -> str | None:
    """计算 span 覆盖的页码范围，若有跨页则返回 "first-last"。"""
    labels = list(
        dict.fromkeys(
            page.page_label
            for page in pages
            if _overlaps(page.source_span, span)
        )
    )
    if not labels:
        return None
    return labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"


def _overlaps(left: SourceSpan, right: SourceSpan) -> bool:
    """判断两个区间是否有重叠（半开区间）。"""
    return left.start_offset < right.end_offset and right.start_offset < left.end_offset
