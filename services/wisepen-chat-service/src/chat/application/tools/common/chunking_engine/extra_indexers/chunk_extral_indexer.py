from __future__ import annotations

import re

from ..core.models import Chunk, ChunkDocument, ChunkIndex, IndexKind, TextUnit, UnitType

# 统一页码标记格式：<!-- page N -->
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")

# 从 Markdown 表格/公式 unit 中提取编号：Table 1: / Equation (3) 等
_TABLE_CAPTION_RE = re.compile(r"^(?:Table|表格)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_FORMULA_LABEL_RE = re.compile(r"(?:Equation|Eq\.?|公式)\s+[\(]?(\d+(?:\.\d+)*)[\)]?", re.IGNORECASE)

# 从图片 alt 文本中提取图号：Figure 1 / Fig. 2 / 图 3
_FIGURE_NUMBER_RE = re.compile(r"^(?:Figure|Fig\.?|图)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)


class ChunkExtraIndexer:
    """chunk 额外语义索引器，专为 Markdown pipeline 构建。

    构建三种额外索引：
    1. section（IndexKind.SECTION）— Markdown heading section 到 chunk 的映射
       用途：按章节名定位 chunk，如 "安装章节包含哪些 chunk"
    2. page（IndexKind.PAGE）— page_marker（<!-- page N -->）到 chunk 的映射
       用途：按页码定位 chunk，如 "第 3 页包含哪些 chunk"
    3. anchor（IndexKind.ANCHOR）— 文档锚标（Table/Figure/Equation）到 chunk 的映射
       用途：按引用定位 chunk，如 "Table 2 在哪个 chunk"

    三种索引均基于 Markdown 结构精确识别（unit_type），无需开关控制：
    - section：来自 HEADING unit
    - page：来自 PAGE_MARKER unit
    - anchor：来自 TABLE / FORMULA / 含图片语法的 PARAGRAPH unit

    注意：Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续索引，无需额外构建。此索引器只构建额外的语义维度索引。

    非 Markdown pipeline（纯文本/连续读取）不应配置 extra_indexer，
    因为 RecursiveTextSplitter 不会产出这些结构化 unit。
    """

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "chunk_extra_indexer"

    def index(
        self,
        *,
        document: ChunkDocument,
        units: tuple[TextUnit, ...],
        chunks: tuple[Chunk, ...],
    ) -> tuple[ChunkIndex, ...]:
        """基于最终 chunk 构建额外语义索引。"""
        indexes: list[ChunkIndex] = []
        indexes.extend(self._build_section_indexes(document, units, chunks))
        indexes.extend(self._build_page_indexes(document, units, chunks))
        indexes.extend(self._build_anchor_indexes(units, chunks))
        return tuple(indexes)

    # -- section -------------------------------------------------------------
    # 找到所有 heading unit，按标题划分 section 范围，
    # 然后找到与该范围重叠的所有 chunk。

    def _build_section_indexes(
        self,
        document: ChunkDocument,
        units: tuple[TextUnit, ...],
        chunks: tuple[Chunk, ...],
    ) -> list[ChunkIndex]:
        headings = [u for u in units if u.unit_type == UnitType.HEADING and u.section_path]
        if not headings:
            return []

        indexes: list[ChunkIndex] = []
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
                indexes.append(ChunkIndex(
                    name=f"section:{section_path}",
                    kind=IndexKind.SECTION,
                    chunk_indices=tuple(c.chunk_index for c in covered),
                    chunk_ids=tuple(c.chunk_id for c in covered),
                    start_offset=section_start,
                    end_offset=section_end,
                    metadata={
                        "section_path": heading.section_path,
                        "title": heading.metadata.get("title", ""),
                    },
                ))
        return indexes

    # -- page ----------------------------------------------------------------
    # 找到所有 page_marker unit，按页码划分 page 范围，
    # 然后找到与该范围重叠的所有 chunk。

    def _build_page_indexes(
        self,
        document: ChunkDocument,
        units: tuple[TextUnit, ...],
        chunks: tuple[Chunk, ...],
    ) -> list[ChunkIndex]:
        page_units = [u for u in units if u.unit_type == UnitType.PAGE_MARKER]
        if not page_units:
            return []

        indexes: list[ChunkIndex] = []
        for page_idx, page_unit in enumerate(page_units):
            page_name = _extract_page_name(page_unit.text)
            # page 范围：从当前页码标记到下一个页码标记之前
            page_end = (
                page_units[page_idx + 1].start_offset
                if page_idx + 1 < len(page_units)
                else len(document.text)
            )
            covered = _chunks_covering_range(chunks, page_unit.start_offset, page_end)
            if covered:
                indexes.append(ChunkIndex(
                    name=f"page:{page_name}",
                    kind=IndexKind.PAGE,
                    chunk_indices=tuple(c.chunk_index for c in covered),
                    chunk_ids=tuple(c.chunk_id for c in covered),
                    start_offset=page_unit.start_offset,
                    end_offset=page_end,
                    metadata={"page_name": page_name},
                ))
        return indexes

    # -- anchor --------------------------------------------------------------
    # 基于 Markdown 结构精确识别锚标，而非正则扫描所有文本：
    # - TABLE unit → 提取表格编号（如 "Table 1: xxx"）
    # - FORMULA unit → 提取公式编号（如 "Equation (3)"）
    # - IMAGE unit → 从 alt 文本提取图号（如 "Figure 2: caption"）

    def _build_anchor_indexes(
        self,
        units: tuple[TextUnit, ...],
        chunks: tuple[Chunk, ...],
    ) -> list[ChunkIndex]:
        indexes: list[ChunkIndex] = []

        for unit in units:
            if unit.unit_type == UnitType.TABLE:
                anchor_name = self._extract_table_anchor(unit.text)
            elif unit.unit_type == UnitType.FORMULA:
                anchor_name = self._extract_formula_anchor(unit.text)
            elif unit.unit_type == UnitType.IMAGE:
                anchor_name = self._extract_figure_anchor(unit)
            else:
                continue

            if anchor_name is None:
                continue

            containing = _chunk_containing_unit(chunks, unit)
            if containing:
                indexes.append(ChunkIndex(
                    name=f"anchor:{anchor_name}",
                    kind=IndexKind.ANCHOR,
                    chunk_indices=(containing.chunk_index,),
                    chunk_ids=(containing.chunk_id,),
                    start_offset=unit.start_offset,
                    end_offset=unit.end_offset,
                    metadata={"anchor": anchor_name},
                ))

        return indexes

    @staticmethod
    def _extract_table_anchor(text: str) -> str | None:
        """从 TABLE unit 首行提取表格锚标，如 'Table 1: 用户表' → 'Table 1'。"""
        first_line = text.split("\n", 1)[0]
        m = _TABLE_CAPTION_RE.match(first_line.strip())
        if m:
            return f"Table {m.group(1)}"
        return None

    @staticmethod
    def _extract_formula_anchor(text: str) -> str | None:
        """从 FORMULA unit 提取公式锚标，如 'Equation (3)' → 'Equation 3'。"""
        m = _FORMULA_LABEL_RE.search(text)
        if m:
            return f"Equation {m.group(1)}"
        return None

    @staticmethod
    def _extract_figure_anchor(unit: TextUnit) -> str | None:
        """从 IMAGE unit 的 alt 文本提取图号，如 'Figure 2: 示意图' → 'Figure 2'。

        alt 文本由 MarkdownBlockSplitter 从 ![alt](url) 中提取，存入 metadata["alt"]。
        """
        alt = unit.metadata.get("alt", "")
        if not alt:
            return None
        m = _FIGURE_NUMBER_RE.match(str(alt).strip())
        if m:
            return f"Figure {m.group(1)}"
        return None


# -- helpers -----------------------------------------------------------------

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


def _chunk_containing_unit(chunks: tuple[Chunk, ...], unit: TextUnit) -> Chunk | None:
    """找到包含指定 unit 的 chunk。

    优先按 unit index 范围匹配（chunk.start_unit <= unit.unit_index <= chunk.end_unit），
    回退到 offset 范围匹配。
    """
    for chunk in chunks:
        if chunk.start_unit is not None and chunk.end_unit is not None:
            if chunk.start_unit <= unit.unit_index <= chunk.end_unit:
                return chunk
        if chunk.start_offset is not None and chunk.end_offset is not None:
            if unit.start_offset is not None and unit.end_offset is not None:
                if chunk.start_offset <= unit.start_offset and chunk.end_offset >= unit.end_offset:
                    return chunk
    return None


def _extract_page_name(text: str) -> str:
    """从 page_marker 文本中提取页码。

    统一格式为 <!-- page N -->，提取 N。
    """
    m = _PAGE_MARKER_RE.match(text.strip())
    return m.group(1) if m else text.strip()
