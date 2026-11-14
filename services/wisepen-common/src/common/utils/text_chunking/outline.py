"""把 Markdown 章节和锚点格式化为全局或邻域导航目录"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .chunking.chunker import Anchor, Section


class OutlineFormatter:
    """共享目录格式和树遍历"""

    def __init__(
        self,
        *,
        sections: Sequence[Section],
        anchors: Sequence[Anchor] | None = None,
        show_extra_info: bool = True,
    ) -> None:
        # 把线性列表变成可遍历的树

        self._sections_by_id = {section.section_id: section for section in sections}  # id → 章节，供 neighborhood 快速定位
        self._has_document_root = any(section.level == 0 for section in sections)  # 是否存在 level=0 的合成根章节，计算#号层级时用

        # 按父节点分组，并按 ordinal 稳定排序，保证目录顺序一致
        self._children_by_parent: dict[str | None, list[Section]] = defaultdict(list)
        for section in sections:
            self._children_by_parent[section.parent_section_id].append(section)
        for children in self._children_by_parent.values():
            children.sort(key=lambda section: section.ordinal)

        self._anchors = anchors or []  # 编号题注，渲染时标注到所属章节
        self._show_extra_info = show_extra_info  # 是否输出 id/锚点/摘要等附加信息

    # DFS 打印整棵树
    def global_outline(self, *, max_level: int = 0) -> str:
        """生成全局目录；根深度为 1，max_level=0 表示展开全部。"""

        lines: list[str] = []  # 累积目录行

        def visit(section: Section, indent: int, depth: int) -> None:
            """深度优先遍历：先渲染当前章节，再递归进入子树"""

            if 0 < max_level < depth:  # max_level=0 表示不限深度
                return
            lines.append(self._node_line(section, indent=indent))
            for child in self._children_by_parent.get(section.section_id, []):
                visit(child, indent + 1, depth + 1)  # 缩进与深度各加一

        for root in self._children_by_parent.get(None, []):  # 顶层章节（parent 为 None）
            visit(root, 0, 1)
        return "\n".join(lines)

    def neighborhood(self, section_id: str, *, sibling_steps: int = 1) -> str:
        """输出以当前章节为中心的局部目录：祖先链 + 兄弟窗口 + 直接子节点"""

        section = self._sections_by_id[section_id]  # 当前章节
        siblings = self._children_by_parent.get(section.parent_section_id, [])  # 同父的兄弟章节
        index = next(
            index
            for index, sibling in enumerate(siblings)
            if sibling.section_id == section_id
        )  # 当前章节在兄弟列表中的位置

        # 以当前章节为中心，向左右各取 sibling_steps 个兄弟
        start = max(0, index - sibling_steps)  # 窗口左边界，不越过 0
        visible_siblings = siblings[start : index + sibling_steps + 1]

        lines: list[str] = []  # 累积目录行
        rendered_ids: set[str] = set()  # 已输出的章节 id，用于去重

        def append_node(
            section: Section,
            *,
            indent: int,
            current: bool = False,
        ) -> None:
            """渲染一行章节，并按 id 去重"""
            if section.section_id in rendered_ids:
                return
            rendered_ids.add(section.section_id)
            lines.append(self._node_line(section, indent=indent, current=current))

        ancestors: list[Section] = []  # 祖先章节（先按 子→父 收集）
        parent_id = section.parent_section_id  # 从直接父节点开始向上回溯
        visited_ids = {section_id}  # 已访问集合，防环

        while parent_id is not None and parent_id not in visited_ids:
            parent = self._sections_by_id.get(parent_id)
            if parent is None:
                break  # 父节点缺失，停止
            ancestors.append(parent)
            visited_ids.add(parent.section_id)
            parent_id = parent.parent_section_id
        ancestors.reverse()  # 反转为 根→父 顺序，输出时从上到下
        for indent, ancestor in enumerate(ancestors):
            append_node(ancestor, indent=indent)

        # 祖先不展开其他分支，兄弟不展开子树；只展开当前章节的直接子节点
        current_indent = len(ancestors)  # 兄弟窗口的缩进层级 = 祖先数量
        for sibling in visible_siblings:
            is_current = sibling.section_id == section_id  # 标记当前章节
            append_node(sibling, indent=current_indent, current=is_current)
            if is_current:
                for child in self._children_by_parent.get(section_id, []):
                    append_node(child, indent=current_indent + 1)  # 直接子节点缩进再加一
        return "\n".join(lines)

    def _node_line(
        self,
        section: Section,
        *,
        indent: int,
        current: bool = False,
    ) -> str:
        """按标题树层级输出标题、状态、锚点、原始 ID 和原文起点"""

        title = section.title.strip()  # 去除标题首尾空白
        # 树深度换算井号数；存在合成根章节时减一（根章节不打井号）
        heading_level = (
            0 if section.level == 0 else indent + 1 - int(self._has_document_root)
        )
        heading = "#" * heading_level  # #号数量即标题层级
        prefix = f"{heading} " if heading else ""  # 根章节无#号，前缀为空

        # 不输出 id/锚点/摘要等附加信息时直接返回
        if not self._show_extra_info:
            return f"{prefix}{title}"

        markers = ["[C]"] if current else []  # 当前章节加 [C] 标记

        # 收集与本章节 own_span 相交的锚点
        anchors = [
            anchor.label
            for anchor in self._anchors
            if anchor.source_span.start_offset < section.own_span.end_offset
            and section.own_span.start_offset < anchor.source_span.end_offset
        ]
        if anchors:
            markers.extend(f"[{anchor}]" for anchor in anchors)  # 锚点以 [图 1] 形式追加
        # id 用于定位章节，@ 后是章节在原文字符起点
        suffix = " ".join(
            [*markers, f"id={section.section_id}", f"@{section.own_span.start_offset}"]
        )
        line = f"{prefix}{title} {suffix}"
        # TODO: 留待日后实现 LLM 的外部摘要
        summary = section.summary.strip()  # 优先展示外部摘要
        if summary:
            return f"{line}\n[Summary]: {summary}"
        if preview := section.preview.strip():  # 否则回退到正文前两句预览
            return f"{line}\n[Preview]: {preview}"
        return line
