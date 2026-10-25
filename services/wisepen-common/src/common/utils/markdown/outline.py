"""把 Markdown 章节和锚点格式化为全局或邻域导航目录。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .chunking.chunker import Anchor, Section


class OutlineFormatter:
    """共享目录格式和树遍历；索引属于当前文档，不负责可见性检查。"""

    def __init__(
        self,
        *,
        sections: Sequence[Section],
        anchors: Sequence[Anchor] | None = None,
        show_extra_info: bool = True,
    ) -> None:
        self._sections_by_id = {section.section_id: section for section in sections}
        self._has_document_root = any(section.level == 0 for section in sections)

        # 按父节点分组，并按 ordinal 稳定排序，保证目录顺序一致
        self._children_by_parent: dict[str | None, list[Section]] = defaultdict(list)
        for section in sections:
            self._children_by_parent[section.parent_section_id].append(section)
        for children in self._children_by_parent.values():
            children.sort(key=lambda section: section.ordinal)

        self._anchors = anchors or []
        self._show_extra_info = show_extra_info

    def global_outline(self, *, max_level: int = 0) -> str:
        """生成全局目录；根深度为 1，max_level=0 表示展开全部。"""

        lines: list[str] = []

        def visit(section: Section, indent: int, depth: int) -> None:
            # 深度取决于 parent_section_id 树，而不是 Markdown 标题级别
            if 0 < max_level < depth:
                return
            lines.append(self._node_line(section, indent=indent))
            for child in self._children_by_parent.get(section.section_id, []):
                visit(child, indent + 1, depth + 1)

        for root in self._children_by_parent.get(None, []):
            visit(root, 0, 1)
        return "\n".join(lines)

    def neighborhood(self, section_id: str, *, sibling_steps: int = 1) -> str:
        """保留完整祖先链、当前层兄弟窗口及当前章节的直接子节点。"""

        section = self._sections_by_id[section_id]
        siblings = self._children_by_parent.get(section.parent_section_id, [])
        index = next(
            index
            for index, sibling in enumerate(siblings)
            if sibling.section_id == section_id
        )

        # 以当前章节为中心，向左右各取 sibling_steps 个兄弟
        start = max(0, index - sibling_steps)
        visible_siblings = siblings[start : index + sibling_steps + 1]

        lines: list[str] = []
        rendered_ids: set[str] = set()

        def append_node(
            section: Section,
            *,
            indent: int,
            current: bool = False,
        ) -> None:
            # 去重：同一章节只输出一次
            if section.section_id in rendered_ids:
                return
            rendered_ids.add(section.section_id)
            lines.append(self._node_line(section, indent=indent, current=current))

        # 回溯父链后反转为根到父节点的顺序；遇到缺失父节点或环时停止
        ancestors: list[Section] = []
        parent_id = section.parent_section_id
        visited_ids = {section_id}
        while parent_id is not None and parent_id not in visited_ids:
            parent = self._sections_by_id.get(parent_id)
            if parent is None:
                break
            ancestors.append(parent)
            visited_ids.add(parent.section_id)
            parent_id = parent.parent_section_id
        ancestors.reverse()
        for indent, ancestor in enumerate(ancestors):
            append_node(ancestor, indent=indent)

        # 祖先不展开其他分支，兄弟不展开子树；只展开当前章节的直接子节点
        current_indent = len(ancestors)
        for sibling in visible_siblings:
            is_current = sibling.section_id == section_id
            append_node(sibling, indent=current_indent, current=is_current)
            if is_current:
                for child in self._children_by_parent.get(section_id, []):
                    append_node(child, indent=current_indent + 1)
        return "\n".join(lines)

    def _node_line(
        self,
        section: Section,
        *,
        indent: int,
        current: bool = False,
    ) -> str:
        """按标题树层级输出标题、状态、锚点、原始 ID 和原文起点。"""

        title = section.title.strip()
        heading_level = (
            0 if section.level == 0 else indent + 1 - int(self._has_document_root)
        )
        heading = "#" * heading_level
        prefix = f"{heading} " if heading else ""
        # 摄入材料只需要标题骨架，不暴露定位信息或派生摘要。
        if not self._show_extra_info:
            return f"{prefix}{title}"

        markers = ["[C]"] if current else []
        # 只收集与本章节 own_span 相交的锚点，避免归入祖先子树。
        anchors = [
            anchor.label
            for anchor in self._anchors
            if anchor.source_span.start_offset < section.own_span.end_offset
            and section.own_span.start_offset < anchor.source_span.end_offset
        ]
        if anchors:
            markers.extend(f"[{anchor}]" for anchor in anchors)
        suffix = " ".join(
            [*markers, f"id={section.section_id}", f"@{section.own_span.start_offset}"]
        )
        line = f"{prefix}{title} {suffix}"
        summary = section.summary.strip()
        if summary:
            return f"{line}\n[Summary]: {summary}"
        if preview := section.preview.strip():
            return f"{line}\n[Preview]: {preview}"
        return line
