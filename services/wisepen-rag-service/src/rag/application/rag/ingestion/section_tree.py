from dataclasses import replace
from hashlib import sha256

from common.utils.chunkers import BlockKind, TextBlock
from .models import RagSectionNode


def build_section_tree(
        blocks: tuple[TextBlock, ...],
        *,
        resource_id: str,
        document_version: int,
        text_length: int,
) -> tuple[RagSectionNode, ...]:
    """根据文档标题块构建层级化 Section 树。

    Section 使用标题作为边界：
    - own_start / own_end 表示当前标题直属覆盖范围；
    - subtree_end 表示整个子树覆盖范围，会在遇到同级或更高层标题时闭合。
    """
    headings = tuple(block for block in blocks if block.block_kind is BlockKind.HEADING)

    # 文档开头到第一个标题之间的内容属于 root section。
    first_heading_start = headings[0].start_offset if headings else text_length
    assert first_heading_start is not None

    # root 节点不参与前缀式 ID 生成，但需要为顶层标题提供一个公共父节点和统一 subtree_end。
    root = RagSectionNode(
        section_id=_section_id(resource_id, document_version, "root"),
        resource_id=resource_id,
        document_version=document_version,
        title="",
        level=0,
        parent_section_id=None,
        ordinal=0,
        section_path=(),
        summary="",
        own_start=0,
        own_end=first_heading_start,
        subtree_end=text_length,
    )

    sections = [root]
    # open_sections 保存当前仍未闭合的标题链（按文档顺序），栈顶即当前父级 Section 的索引。
    # 每次遇到新标题时按 level 弹栈，从而让父级 subtree_end 收敛到第一个同级/上级标题处。
    open_sections: list[int] = []
    # 记录每个父节点下已创建的子节点数，用于稳定排序。
    child_counts: dict[str, int] = {}

    for heading_index, heading in enumerate(headings):
        assert heading.start_offset is not None
        level = int(heading.metadata["heading_level"])

        # 标题层级 >= 当前层级时，意味着同级或更高级别出现了新章节，原节点应在此标题处闭合。
        while open_sections and sections[open_sections[-1]].level >= level:
            closed_index = open_sections.pop()
            sections[closed_index] = replace(sections[closed_index], subtree_end=heading.start_offset)

        parent = sections[open_sections[-1]] if open_sections else root
        ordinal = child_counts.get(parent.section_id, 0)
        child_counts[parent.section_id] = ordinal + 1

        # own 范围从当前标题开始，到下一个标题开始（不区分层级）为止。
        next_heading_start = (
            headings[heading_index + 1].start_offset
            if heading_index + 1 < len(headings)
            else text_length
        )
        assert next_heading_start is not None

        # 新建节点的 subtree_end 默认延伸到文档末尾，遇到同级/上级标题时由弹栈逻辑负责收敛。
        section = RagSectionNode(
            section_id=_section_id(resource_id, document_version, str(heading.start_offset)),
            resource_id=resource_id,
            document_version=document_version,
            title=str(heading.metadata["title"]),
            level=level,
            parent_section_id=parent.section_id,
            ordinal=ordinal,
            section_path=heading.section_path,
            summary="",
            own_start=heading.start_offset,
            own_end=next_heading_start,
            subtree_end=text_length,
        )
        sections.append(section)
        open_sections.append(len(sections) - 1)

    return tuple(sections)


def _section_id(resource_id: str, document_version: int, source_key: str) -> str:
    """生成稳定的 Section ID。

    不依赖数据库自增 ID，保证同一文档版本重复解析时结果一致。
    """
    value = "\0".join((resource_id, str(document_version), "section", source_key))
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"rsec_{digest[:32]}"
