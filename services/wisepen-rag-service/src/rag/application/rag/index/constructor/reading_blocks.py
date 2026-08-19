"""从文档结构构建稳定的 ReadingBlock。

ReadingBlock 是供模型阅读的中等粒度父块：
- SECTIONED 文档可合并文档顺序上连续、且属于同一父 Section 的正文片段。
- 每个 ReadingBlock 持有有序 Section ID 和原文 span，可严格回源。
- 5000 字符是期望阅读长度，6000 字符是任何父块都不得突破的硬上限。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from common.utils.document import (
    BlockKind,
    DocumentBlock,
    DocumentChunker,
    DocumentChunkerConfig,
    Section,
    SourceSpan,
)

from rag.domain.models.content import ReadingBlock
from rag.domain.models.structure import DocumentStructure, StructureMode

from ._source_spans import _overlaps, _render_source_text

_READING_BLOCK_TARGET_CHARACTERS = 5000
_READING_BLOCK_MAX_CHARACTERS = 6000
_RENDERED_SEPARATOR_LENGTH = 2


@dataclass(slots=True)
class _ReadingAtom:
    """不可跨 Section 的最小父块装箱单元。"""

    section_id: str
    parent_section_id: str | None
    source_span: SourceSpan


def build_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """按结构模式生成 ReadingBlock；empty 文档不产生伪正文块。"""
    if structure.mode is StructureMode.EMPTY:
        return []

    build = (
        _build_section_reading_blocks
        if structure.mode is StructureMode.SECTIONED
        else _build_flat_text_reading_blocks
    )
    return build(
        resource_id=resource_id,
        content_revision=content_revision,
        markdown=markdown,
        structure=structure,
        sections=sections,
    )


def _build_section_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """合并连续的兄弟 Section，并按软目标和硬上限组装父块。"""
    atoms = _build_reading_atoms(markdown, sections)
    atom_groups = _group_adjacent_siblings(atoms)
    span_groups = [
        span_group
        for atom_group in atom_groups
        for span_group in _pack_atoms(atom_group)
    ]
    return [
        _reading_block(
            resource_id=resource_id,
            content_revision=content_revision,
            markdown=markdown,
            section_ids=list(dict.fromkeys(atom.section_id for atom in group)),
            ordinal=ordinal,
            source_spans=[atom.source_span for atom in group],
            structure=structure,
        )
        for ordinal, group in enumerate(span_groups)
    ]


def _build_reading_atoms(markdown: str, sections: list[Section]) -> list[_ReadingAtom]:
    """把 Section 正文拆成不超过硬上限、仍保留所属关系的原文片段。"""
    chunker = DocumentChunker(
        DocumentChunkerConfig(max_characters=_READING_BLOCK_MAX_CHARACTERS)
    )
    atoms: list[_ReadingAtom] = []

    for section in sections:
        if section.own_span.start_offset == section.own_span.end_offset:
            continue
        section_text = markdown[
            section.own_span.start_offset : section.own_span.end_offset
        ]
        result = chunker.chunk(section_text)
        heading_end = _heading_end_offset(result.blocks, section)

        # chunker 可能在超长正文块内部继续拆分；逐 span 装箱才能在尾块过短时重平衡。
        for chunk in result.chunks:
            atoms.extend(
                _ReadingAtom(
                    section_id=section.section_id,
                    parent_section_id=section.parent_section_id,
                    source_span=SourceSpan(
                        section.own_span.start_offset + span.start_offset,
                        section.own_span.start_offset + span.end_offset,
                    ),
                )
                for span in chunk.source_spans
                if span.end_offset > heading_end
            )
    return atoms


def _group_adjacent_siblings(atoms: list[_ReadingAtom]) -> list[list[_ReadingAtom]]:
    """只允许文档顺序连续且 parent_section_id 相同的 Section 共享父块。"""
    groups: list[list[_ReadingAtom]] = []
    for atom in atoms:
        if not groups or groups[-1][-1].parent_section_id != atom.parent_section_id:
            groups.append([atom])
        else:
            groups[-1].append(atom)
    return groups


def _pack_atoms(atoms: list[_ReadingAtom]) -> list[list[_ReadingAtom]]:
    """先按 5000 字符软目标装箱，再对过短尾块做相邻重平衡。"""
    packed: list[list[_ReadingAtom]] = []
    current: list[_ReadingAtom] = []

    for atom in atoms:
        if current and (
            _rendered_length(current) >= _READING_BLOCK_TARGET_CHARACTERS
            or _rendered_length([*current, atom]) > _READING_BLOCK_MAX_CHARACTERS
        ):
            packed.append(current)
            current = []
        current.append(atom)
    if current:
        packed.append(current)

    # 软目标不是最小值。尾块无法直接并入前块时，在原文 span 边界上重新选切点，
    # 避免产生一个接近 5000 字符的父块和一个极短阅读块。
    for index in range(len(packed) - 1, 0, -1):
        if _rendered_length(packed[index]) >= _READING_BLOCK_TARGET_CHARACTERS:
            continue
        combined = [*packed[index - 1], *packed[index]]
        if _rendered_length(combined) <= _READING_BLOCK_MAX_CHARACTERS:
            packed[index - 1] = combined
            del packed[index]
            continue
        left, right = _balanced_split(combined)
        packed[index - 1], packed[index] = left, right
    return packed


def _balanced_split(
    atoms: list[_ReadingAtom],
) -> tuple[list[_ReadingAtom], list[_ReadingAtom]]:
    """在两个父块都不超硬上限的前提下，选择最接近软目标的切点。"""
    candidates = [
        (atoms[:index], atoms[index:])
        for index in range(1, len(atoms))
        if _rendered_length(atoms[:index]) <= _READING_BLOCK_MAX_CHARACTERS
        and _rendered_length(atoms[index:]) <= _READING_BLOCK_MAX_CHARACTERS
    ]
    if not candidates:
        return atoms[:-1], atoms[-1:]
    return min(
        candidates,
        key=lambda pair: abs(_rendered_length(pair[0]) - _READING_BLOCK_TARGET_CHARACTERS)
        + abs(_rendered_length(pair[1]) - _READING_BLOCK_TARGET_CHARACTERS),
    )


def _rendered_length(atoms: list[_ReadingAtom]) -> int:
    content_length = sum(
        atom.source_span.end_offset - atom.source_span.start_offset for atom in atoms
    )
    return content_length + max(0, len(atoms) - 1) * _RENDERED_SEPARATOR_LENGTH


def _build_flat_text_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """FLAT_TEXT 的合成 Section 已按 content_spans 切好，直接复用其边界。"""
    return [
        _reading_block(
            resource_id=resource_id,
            content_revision=content_revision,
            markdown=markdown,
            section_ids=[section.section_id],
            ordinal=ordinal,
            source_spans=list(section.content_spans),
            structure=structure,
        )
        for ordinal, section in enumerate(sections)
    ]


def _heading_end_offset(
    parsed_blocks: Sequence[DocumentBlock],
    section: Section,
) -> int:
    """计算 Section 标题在 section 局部坐标中的结束偏移。"""
    if section.level == 0:
        return 0
    first_block = parsed_blocks[0] if parsed_blocks else None
    if first_block is None or first_block.block_kind is not BlockKind.HEADING:
        raise ValueError(f"section {section.section_id} does not start with its heading")
    return first_block.end_offset


def _reading_block(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    section_ids: list[str],
    ordinal: int,
    source_spans: list[SourceSpan],
    structure: DocumentStructure,
) -> ReadingBlock:
    """组装单个 ReadingBlock 实例。"""
    return ReadingBlock(
        block_id=_build_reading_block_id(
            resource_id=resource_id,
            content_revision=content_revision,
            section_ids=section_ids,
            source_spans=source_spans,
        ),
        section_ids=section_ids,
        ordinal=ordinal,
        raw_text=_render_source_text(markdown, source_spans),
        source_spans=source_spans,
        page_labels=[
            page.page_label
            for page in structure.pages
            if _overlaps(page.source_span, source_spans)
        ],
        anchor_labels=[
            anchor.label
            for anchor in structure.anchors
            if _overlaps(anchor.source_span, source_spans)
        ],
    )


def _build_reading_block_id(
    *,
    resource_id: str,
    content_revision: str,
    section_ids: list[str],
    source_spans: list[SourceSpan],
) -> str:
    """基于资源、revision、Section 序列和 span 边界生成稳定 ID。"""
    span_identity = ";".join(
        f"{span.start_offset}:{span.end_offset}" for span in source_spans
    )
    section_identity = ";".join(section_ids)
    identity = f"{resource_id}\0{content_revision}\0{section_identity}\0{span_identity}"
    return f"rsb_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
