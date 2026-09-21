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
        """为每个 section 生成带完整祖先链的局部大纲。"""
        locations = await self._snapshots.locate_sections(section_ids, scope=scope)

        items: list[NeighborhoodItem] = []
        # 同一请求可能定位到同一文档的多个 Section；这些结构索引只需构建一次。
        document_indexes: dict[
            str,
            tuple[dict[str, Section], dict[str | None, list[Section]]],
        ] = {}
        for section_id in section_ids:
            location = locations.get(section_id)
            if location is None:
                # 不存在、旧 revision 和无权 Section 对外必须不可区分。
                raise DocumentReadError("section is not visible")

            structure = location.document.structure
            indexes = document_indexes.get(location.document.resource_id)
            if indexes is None:
                sections_by_id = {
                    section.section_id: section for section in structure.sections
                }
                indexes = (sections_by_id, _children_by_parent(structure.sections))
                document_indexes[location.document.resource_id] = indexes
            sections_by_id, children_by_parent = indexes

            siblings = children_by_parent.get(
                location.section.parent_section_id, []
            )
            index = next(
                index
                for index, sibling in enumerate(siblings)
                if sibling.section_id == section_id
            )

            # 取邻域窗口内的兄弟节点
            start = max(0, index - sibling_steps)
            end = index + sibling_steps + 1
            visible_siblings = siblings[start:end]

            lines: list[str] = []
            rendered_ids: set[str] = set()

            def append_node(
                section: Section,
                *,
                indent: int,
                current: bool = False,
                rendered_ids: set[str] = rendered_ids,
                lines: list[str] = lines,
                structure=structure,
                children_by_parent=children_by_parent,
            ) -> None:
                # 有效 Section Tree 不会重复引用节点；集合同时保护输出不重复。
                if section.section_id in rendered_ids:
                    return
                rendered_ids.add(section.section_id)
                lines.append(
                    _node_line(
                        structure,
                        section,
                        indent=indent,
                        current=current,
                        children_by_parent=children_by_parent,
                    )
                )

            # 沿 parent_section_id 回溯，再反转为根到父节点的真实缩进路径。
            ancestors: list[Section] = []
            parent_id = location.section.parent_section_id
            visited_ids = {location.section.section_id}
            while parent_id is not None and parent_id not in visited_ids:
                parent = sections_by_id.get(parent_id)
                if parent is None:
                    break
                ancestors.append(parent)
                visited_ids.add(parent.section_id)
                parent_id = parent.parent_section_id
            ancestors.reverse()
            for indent, ancestor in enumerate(ancestors):
                append_node(ancestor, indent=indent)

            current_indent = len(ancestors)
            # 当前层只保留 sibling_steps 窗口，不展开祖先节点的其他分支。
            for sibling in visible_siblings:
                is_current = sibling.section_id == section_id
                append_node(
                    sibling,
                    indent=current_indent,
                    current=is_current,
                )
                if is_current:
                    for child in children_by_parent.get(section_id, []):
                        append_node(child, indent=current_indent + 1)

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
        """生成整个文档的全局大纲，限制相对于根节点的最大深度。"""
        documents = await self._snapshots.load_documents([resource_id], scope=scope)
        document = documents.get(resource_id)
        if document is None:
            raise DocumentReadError("document is not visible")

        children_by_parent = _children_by_parent(document.structure.sections)
        lines: list[str] = []

        def visit(section: Section, indent: int, depth: int) -> None:
            # max_level 表示相对于目录根节点的树深度，而非 Markdown 标题级别。
            if max_level > 0 and depth > max_level:
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
                visit(child, indent + 1, depth + 1)

        # 从根节点（parent_section_id 为 None）开始遍历
        for root in children_by_parent.get(None, []):
            visit(root, 0, 1)

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
    marker = (
        f" {{#{section.section_id}}} [current]"
        if current
        else f" {{#{section.section_id}}}"
    )

    page_range = _page_range(structure.pages, section.subtree_span)
    # 字符数对应默认 DIRECT 读取的直属正文；页码则表示包含子章节的整体覆盖范围。
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
