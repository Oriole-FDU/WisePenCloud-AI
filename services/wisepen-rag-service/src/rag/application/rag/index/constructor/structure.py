"""将权威正文投影为 RAG 使用的 Common 文档结构事实。"""

from common.utils.document import (
    BlockKind,
    DocumentBlock,
    DocumentChunker,
    DocumentChunkerConfig,
)

from rag.domain.models.structure import DocumentStructure, StructureMode

# 合成 Section 与 ReadingBlock 使用同一预算，保证无标题正文只切分一次。
_FLAT_TEXT_SECTION_MAX_CHARACTERS = 4000


def build_document_structure(markdown: str) -> DocumentStructure:
    """解析正文，并在 RAG 边界补充结构模式和页标签唯一性约束。"""
    result = DocumentChunker(
        DocumentChunkerConfig(
            max_characters=_FLAT_TEXT_SECTION_MAX_CHARACTERS,
        )
    ).chunk(markdown)
    mode = _structure_mode(result.blocks)

    seen_page_labels: set[str] = set()
    for page in result.pages:
        if page.page_label in seen_page_labels:
            raise ValueError(f"duplicate page label: {page.page_label}")
        seen_page_labels.add(page.page_label)

    return DocumentStructure(
        mode=mode,
        total_length=len(markdown),
        sections=[] if mode is StructureMode.EMPTY else list(result.sections),
        pages=list(result.pages),
        anchors=list(result.anchors),
    )


def _structure_mode(blocks: tuple[DocumentBlock, ...]) -> StructureMode:
    if any(block.block_kind is BlockKind.HEADING for block in blocks):
        return StructureMode.SECTIONED
    if any(
        block.block_kind is not BlockKind.PAGE_MARKER and block.text.strip()
        for block in blocks
    ):
        return StructureMode.FLAT_TEXT
    return StructureMode.EMPTY
