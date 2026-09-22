from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .models import Anchor, OutlineNode, Page, Section, SourceSpan


class OutlineAssembler:
    """把内部文档结构投影为不含 offset/path 的模型可见目录。"""

    @staticmethod
    def assemble(
        *,
        sections: Sequence[Section],
        pages: Sequence[Page],
        anchors: Sequence[Anchor],
    ) -> list[OutlineNode]:
        if not sections:
            return []

        # 先按 parent_section_id 建索引，再统一按 ordinal/原文位置排序；
        # outline 不依赖调用方传入顺序，也不使用 section_path 识别节点。
        children_by_parent: dict[str | None, list[Section]] = defaultdict(list)
        root_section: Section | None = None
        for section in sections:
            children_by_parent[section.parent_section_id].append(section)
            if section.parent_section_id is None and section.level == 0:
                root_section = section

        for children in children_by_parent.values():
            children.sort(
                key=lambda section: (
                    section.ordinal,
                    section.own_span.start_offset,
                )
            )

        if root_section is None:
            # 没有前置无标题正文时，真实顶层 Section 直接挂在文档根下。
            return [
                _to_outline_node(
                    section=section,
                    children_by_parent=children_by_parent,
                    pages=pages,
                    anchors=anchors,
                )
                for section in children_by_parent[None]
            ]

        nodes: list[OutlineNode] = []
        if root_section.title:
            # 前言 root 的 subtree 覆盖全文；只投影它自己的 anchor/page，
            # 不展开子标题，避免把整棵文档树重复嵌入“文档开头”。
            nodes.append(
                _to_outline_node(
                    section=root_section,
                    children_by_parent=children_by_parent,
                    pages=pages,
                    anchors=anchors,
                    expand_children=False,
                )
            )
        nodes.extend(
            _to_outline_node(
                section=section,
                children_by_parent=children_by_parent,
                pages=pages,
                anchors=anchors,
            )
            for section in children_by_parent[root_section.section_id]
        )
        return nodes


def _to_outline_node(
    *,
    section: Section,
    children_by_parent: dict[str | None, list[Section]],
    pages: Sequence[Page],
    anchors: Sequence[Anchor],
    expand_children: bool = True,
) -> OutlineNode:
    # 节点长度与页范围共用可见范围：真实章节覆盖子树，前言 root 只覆盖直属正文。
    span = section.subtree_span if section.level > 0 else section.own_span
    page_labels = [
        page.page_label
        for page in pages
        if _overlaps(span, page.source_span)
    ]
    anchor_labels = [
        anchor.label
        for anchor in anchors
        if _overlaps(section.own_span, anchor.source_span)
    ]
    # anchor 必须与 Section 的直属范围相交，不能因为落在子 Section 中而重复归属。
    return OutlineNode(
        section_id=section.section_id,
        title=section.title,
        length=span.end_offset - span.start_offset,
        page_range=_format_page_range(page_labels),
        anchor_labels=anchor_labels,
        children=[
            _to_outline_node(
                section=child,
                children_by_parent=children_by_parent,
                pages=pages,
                anchors=anchors,
            )
            for child in (
                children_by_parent.get(section.section_id, [])
                if expand_children
                else []
            )
        ],
    )


def _format_page_range(page_labels: Sequence[str]) -> str | None:
    # 去重后保留原文顺序，只暴露首尾页标签；内部 page span 不出现在 outline 契约中。
    labels = list(dict.fromkeys(page_labels))
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} - {labels[-1]}"


def _overlaps(span: SourceSpan, other: SourceSpan) -> bool:
    return (
        span.start_offset < other.end_offset
        and span.end_offset > other.start_offset
    )
