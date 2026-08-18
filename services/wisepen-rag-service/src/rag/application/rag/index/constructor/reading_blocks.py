"""从文档结构构建稳定的 ReadingBlock。

ReadingBlock 是介于 Section 和 RetrivalChunk 之间的中等粒度文本单元：
- 每个 ReadingBlock 属于且仅属于一个 Section。
- 每个 ReadingBlock 持有原文 span 列表，可严格回源到权威 Markdown。
- ReadingBlock 内文本不超过 _READING_BLOCK_MAX_CHARACTERS，便于下游抽取窗口复用。
"""

from collections.abc import Sequence
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

# 单个 ReadingBlock 的最大字符数，与下游窗口抽取的最大上下文长度对齐。
_READING_BLOCK_MAX_CHARACTERS = 4000


def build_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """按结构模式生成 ReadingBlock；empty 文档不产生伪正文块。"""
    # EMPTY 文档没有可索引正文，强制不产生 block，避免下游误用空内容。
    if structure.mode is StructureMode.EMPTY:
        return []

    build = (
        _build_section_reading_blocks if structure.mode is StructureMode.SECTIONED
         else _build_flat_text_reading_blocks
    )
    blocks = build(
            resource_id=resource_id,
            content_revision=content_revision,
            markdown=markdown,
            structure=structure,
            sections=sections,
        )

    return blocks


def _build_section_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """SECTIONED 模式下按 Section 切分 ReadingBlock。"""
    blocks: list[ReadingBlock] = []
    chunker = DocumentChunker(
        DocumentChunkerConfig(max_characters=_READING_BLOCK_MAX_CHARACTERS)
    )

    for section in sections:
        # 跳过空 Section。
        if section.own_span.start_offset == section.own_span.end_offset:
            continue

        section_text = markdown[
            section.own_span.start_offset : section.own_span.end_offset
        ]
        result = chunker.chunk(section_text)
        
        # 仅保留标题之后的正文 span，避免标题文字污染检索文本。
        heading_end = _heading_end_offset(result.blocks, section)
        ordinal = 0

        for chunk in result.chunks:
            # chunk.source_spans 是 section 局部坐标；转回 markdown 全局坐标，
            # 并过滤掉落在标题范围内的 span。
            source_spans = [
                SourceSpan(
                    section.own_span.start_offset + span.start_offset,
                    section.own_span.start_offset + span.end_offset,
                )
                for span in chunk.source_spans
                if span.end_offset > heading_end
            ]
            if not source_spans:
                continue
            blocks.append(
                _reading_block(
                    resource_id=resource_id,
                    content_revision=content_revision,
                    markdown=markdown,
                    section=section,
                    ordinal=ordinal,
                    source_spans=source_spans,
                    structure=structure,
                )
            )
            ordinal += 1

    return blocks


def _build_flat_text_reading_blocks(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
) -> list[ReadingBlock]:
    """FLAT_TEXT 模式下生成 ReadingBlock。

    FLAT_TEXT 的合成 Section 已在 build_document_structure 中按同一组
    span_groups 切好，section.content_spans 就是当初的切分结果，直接复用。
    """
    return [
        _reading_block(
            resource_id=resource_id,
            content_revision=content_revision,
            markdown=markdown,
            section=section,
            ordinal=0,
            source_spans=list(section.content_spans),
            structure=structure,
        )
        for section in sections
    ]


def _heading_end_offset(
    parsed_blocks: Sequence[DocumentBlock],
    section: Section,
) -> int:
    """计算 Section 标题在 section 局部坐标中的结束偏移。"""
    # 根 section 没有标题，返回 0（即不跳过任何文本）
    if section.level == 0:
        return 0
    # 校验 section 必须以一个 HEADING block 开头
    first_block = parsed_blocks[0] if parsed_blocks else None
    if first_block is None or first_block.block_kind is not BlockKind.HEADING:
        raise ValueError(
            f"section {section.section_id} does not start with its heading"
        )
    return first_block.end_offset


def _reading_block(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
    section: Section,
    ordinal: int,
    source_spans: list[SourceSpan],
    structure: DocumentStructure,
) -> ReadingBlock:
    """组装单个 ReadingBlock 实例。"""
    return ReadingBlock(
        block_id=_build_reading_block_id(
            resource_id=resource_id,
            content_revision=content_revision,
            section_id=section.section_id,
            source_spans=source_spans,
        ),
        section_id=section.section_id,
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
    section_id: str,
    source_spans: list[SourceSpan],
) -> str:
    """基于资源、revision、Section、span 边界生成稳定的 ReadingBlock ID。"""
    span_identity = ";".join(
        f"{span.start_offset}:{span.end_offset}" for span in source_spans
    )
    identity = f"{resource_id}\0{content_revision}\0{section_id}\0{span_identity}"
    return f"rsb_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
