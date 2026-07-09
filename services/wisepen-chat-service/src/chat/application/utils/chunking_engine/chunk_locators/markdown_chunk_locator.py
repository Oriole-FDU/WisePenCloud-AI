from __future__ import annotations

import re

from ..models import BlockKind, Chunk, ChunkDocument, ChunkLocator, LocatorKind, TextBlock

# 统一页码标记格式：<!-- page N -->
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")

# 从 Markdown 表格/公式 block 中提取编号：Table 1: / Equation (3) 等
_TABLE_CAPTION_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*(?:Table|表格|表)\s+(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
_FORMULA_LABEL_RE = re.compile(r"(?:Equation|Eq\.?|公式)\s+[\(]?(\d+(?:\.\d+)*)[\)]?", re.IGNORECASE)

# 从图片 alt 文本中提取图号：Figure 1 / Fig. 2 / 图 3
_FIGURE_NUMBER_RE = re.compile(r"^(?:Figure|Fig\.?|图)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)


class MarkdownChunkLocator:
    """Markdown chunk 定位器。

    构建三种语义定位索引：
    1. section（LocatorKind.SECTION）— Markdown heading section 到 chunk 的映射
       用途：按章节名定位 chunk，如 "安装章节包含哪些 chunk"
    2. page（LocatorKind.PAGE）— page_marker（<!-- page N -->）到 chunk 的映射
       用途：按页码定位 chunk，如 "第 3 页包含哪些 chunk"
    3. anchor（LocatorKind.ANCHOR）— 文档锚标（Table/Figure/Equation）到 chunk 的映射
       用途：按引用定位 chunk，如 "Table 2 在哪个 chunk"

    三种索引均基于 Markdown 结构精确识别（block_kind），无需开关控制：
    - section：来自 HEADING block
    - page：来自 PAGE_MARKER block
    - anchor：来自 TABLE / FORMULA / IMAGE block

    注意：Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续索引，无需额外构建。此索引器只构建额外的语义维度索引。

    非 Markdown pipeline（纯文本/连续读取）不应配置 chunk_locator，
    因为 RecursiveTextBlockSplitter 不会产出这些结构化 block。
    """

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "markdown_chunk_locator"

    def index(
            self,
            *,
            document: ChunkDocument,
            blocks: tuple[TextBlock, ...],
            chunks: tuple[Chunk, ...],
    ) -> tuple[ChunkLocator, ...]:
        """基于最终 chunk 构建语义定位索引。"""
        locators: list[ChunkLocator] = []
        locators.extend(self._build_section_locators(document, blocks, chunks))
        locators.extend(self._build_page_locators(document, blocks, chunks))
        locators.extend(self._build_anchor_locators(blocks, chunks))
        return tuple(locators)

    # -- section -------------------------------------------------------------
    # 找到所有 heading block，按标题划分 section 范围，
    # 然后找到与该范围重叠的所有 chunk。

    def _build_section_locators(
            self,
            document: ChunkDocument,
            blocks: tuple[TextBlock, ...],
            chunks: tuple[Chunk, ...],
    ) -> list[ChunkLocator]:
        headings = [block for block in blocks if block.block_kind == BlockKind.HEADING and block.section_path]
        if not headings:
            return []

        locators: list[ChunkLocator] = []
        for i, heading in enumerate(headings):
            # section 范围：从当前标题到下一个标题之前
            section_start = heading.start_offset
            section_end = (
                headings[i + 1].start_offset
                if i + 1 < len(headings)
                else len(document.text)
            )
            # 找到与 [section_start, section_end) 有重叠的 chunk
            covered = _chunks_covering_range(chunks, section_start, section_end)
            if covered:
                section_path = " > ".join(heading.section_path)
                locators.append(ChunkLocator(
                    name=f"section:{section_path}",
                    kind=LocatorKind.SECTION,
                    chunk_indices=tuple(c.chunk_index for c in covered),
                    chunk_ids=tuple(c.chunk_id for c in covered),
                    start_offset=section_start,
                    end_offset=section_end,
                    metadata={"section_path": heading.section_path},
                ))
        return locators

    # -- page ----------------------------------------------------------------
    # 找到所有 page_marker block，按页码划分 page 范围，
    # 然后找到与该范围重叠的所有 chunk。

    def _build_page_locators(
            self,
            document: ChunkDocument,
            blocks: tuple[TextBlock, ...],
            chunks: tuple[Chunk, ...],
    ) -> list[ChunkLocator]:
        page_blocks = [block for block in blocks if block.block_kind == BlockKind.PAGE_MARKER]
        if not page_blocks:
            return []

        locators: list[ChunkLocator] = []
        for page_idx, page_block in enumerate(page_blocks):
            page_label = _extract_page_label(page_block.text)
            # page 范围：从当前页码标记到下一个页码标记之前
            page_end = (
                page_blocks[page_idx + 1].start_offset
                if page_idx + 1 < len(page_blocks)
                else len(document.text)
            )
            covered = _chunks_covering_range(chunks, page_block.start_offset, page_end)
            if covered:
                locators.append(ChunkLocator(
                    name=f"page:{page_label}",
                    kind=LocatorKind.PAGE,
                    chunk_indices=tuple(c.chunk_index for c in covered),
                    chunk_ids=tuple(c.chunk_id for c in covered),
                    start_offset=page_block.start_offset,
                    end_offset=page_end,
                    metadata={"page_label": page_label},
                ))
        return locators

    # -- anchor --------------------------------------------------------------
    # 基于 Markdown 结构精确识别锚标，而非正则扫描所有文本：
    # - TABLE block → 提取表格编号（如 "Table 1: xxx"）
    # - FORMULA block → 提取公式编号（如 "Equation (3)"）
    # - IMAGE block → 从 alt 文本提取图号（如 "Figure 2: caption"）

    def _build_anchor_locators(
            self,
            blocks: tuple[TextBlock, ...],
            chunks: tuple[Chunk, ...],
    ) -> list[ChunkLocator]:
        locators: list[ChunkLocator] = []

        for block in blocks:
            if block.block_kind == BlockKind.TABLE:
                anchor_name = self._extract_table_anchor(block)
            elif block.block_kind == BlockKind.FORMULA:
                anchor_name = self._extract_formula_anchor(block.text)
            elif block.block_kind == BlockKind.IMAGE:
                anchor_name = self._extract_figure_anchor(block)
            else:
                continue

            if anchor_name is None:
                continue

            containing = _chunk_containing_block(chunks, block)
            if containing:
                locators.append(ChunkLocator(
                    name=f"anchor:{anchor_name}",
                    kind=LocatorKind.ANCHOR,
                    chunk_indices=(containing.chunk_index,),
                    chunk_ids=(containing.chunk_id,),
                    start_offset=block.start_offset,
                    end_offset=block.end_offset,
                    metadata={"anchor_label": anchor_name},
                ))

        return locators

    @staticmethod
    def _extract_table_anchor(block: TextBlock) -> str | None:
        """从 TABLE block 首行提取表格锚标，如 'Table 1: 用户表' → 'Table 1'。"""
        anchor_label = block.metadata.get("anchor_label")
        if isinstance(anchor_label, str) and anchor_label:
            return anchor_label

        first_line = block.text.split("\n", 1)[0]
        m = _TABLE_CAPTION_RE.match(first_line.strip())
        if m:
            return f"Table {m.group(1)}"
        return None

    @staticmethod
    def _extract_formula_anchor(text: str) -> str | None:
        """从 FORMULA block 提取公式锚标，如 'Equation (3)' → 'Equation 3'。"""
        m = _FORMULA_LABEL_RE.search(text)
        if m:
            return f"Equation {m.group(1)}"
        return None

    @staticmethod
    def _extract_figure_anchor(block: TextBlock) -> str | None:
        """从 IMAGE block 的 alt 文本提取图号，如 'Figure 2: 示意图' → 'Figure 2'。

        alt 文本由 MarkdownBlockSplitter 从 ![alt](url) 中提取，存入 metadata["alt"]。
        """
        alt = block.metadata.get("alt", "")
        if not alt:
            return None
        m = _FIGURE_NUMBER_RE.match(str(alt).strip())
        if m:
            return f"Figure {m.group(1)}"
        return None


# -- utils -----------------------------------------------------------------

def _chunks_covering_range(
        chunks: tuple[Chunk, ...],
        start: int | None,
        end: int | None,
) -> list[Chunk]:
    """找到与 [start, end) 有重叠的 chunk。

    判断条件：chunk.start_offset < end 且 chunk.end_offset > start。
    """
    if start is None:
        return []
    result: list[Chunk] = []
    for chunk in chunks:
        if chunk.start_offset is None or chunk.end_offset is None:
            continue
        if chunk.start_offset < (end or float("inf")) and chunk.end_offset > start:
            result.append(chunk)
    return result


def _chunk_containing_block(chunks: tuple[Chunk, ...], block: TextBlock) -> Chunk | None:
    """找到包含指定 block 的 chunk。

    优先按 block index 范围匹配（chunk.start_block <= block.block_index <= chunk.end_block），
    回退到 offset 范围匹配。
    """
    for chunk in chunks:
        if chunk.start_block is not None and chunk.end_block is not None:
            if chunk.start_block <= block.block_index <= chunk.end_block:
                return chunk
        if chunk.start_offset is not None and chunk.end_offset is not None:
            if block.start_offset is not None and block.end_offset is not None:
                if chunk.start_offset <= block.start_offset and chunk.end_offset >= block.end_offset:
                    return chunk
    return None


def _extract_page_label(text: str) -> str:
    """从 page_marker 文本中提取页码。

    统一格式为 <!-- page N -->，提取 N。
    """
    m = _PAGE_MARKER_RE.match(text.strip())
    return m.group(1) if m else text.strip()
