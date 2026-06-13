from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..core.models import ChunkDocument, TextUnit, UnitType

# 统一页码标记格式：<!-- page N -->
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")

_TOKEN_TO_UNIT_TYPE: dict[str, UnitType] = {
    "heading_open":      UnitType.HEADING,
    "fence":             UnitType.CODE,
    "code_block":        UnitType.CODE,
    "table_open":        UnitType.TABLE,
    "blockquote_open":   UnitType.QUOTE,
    "bullet_list_open":  UnitType.LIST,
    "ordered_list_open": UnitType.LIST,
    "paragraph_open":    UnitType.PARAGRAPH,
    "math_block":        UnitType.FORMULA,
}

_BLOCK_OPENERS = frozenset(_TOKEN_TO_UNIT_TYPE)


class MarkdownBlockSplitter:
    """按 Markdown 结构切分文档，产出结构化 TextUnit。

    识别的 unit 类型：HEADING / PARAGRAPH / TABLE / CODE / FORMULA / LIST / QUOTE / IMAGE / PAGE_MARKER。

    图片处理：markdown-it 将图片解析为 inline token（在 paragraph 内部），
    本 splitter 将其提取为独立的 IMAGE unit，alt 文本存入 metadata["alt"]。
    """

    __slots__ = ("name", "_md")

    def __init__(self) -> None:
        self.name = "markdown_block_splitter"
        self._md = MarkdownIt().use(dollarmath_plugin)

    def split(self, *, document: ChunkDocument) -> tuple[TextUnit, ...]:
        text = document.text
        if not text:
            return ()

        # 预计算每行的起始 offset
        lines = text.splitlines(keepends=True)
        line_start = [0] * (len(lines) + 1)
        for i, line in enumerate(lines):
            line_start[i + 1] = line_start[i] + len(line)

        # 先扫描页码标记行（<!-- page N -->），markdown-it 不会识别为独立 token
        page_marker_units: list[TextUnit] = []
        for i, line in enumerate(lines):
            m = _PAGE_MARKER_RE.match(line)
            if m:
                page_marker_units.append(
                    TextUnit(
                        unit_id=f"unit-pm-{len(page_marker_units)}",
                        text=line.strip(),
                        unit_type=UnitType.PAGE_MARKER,
                        unit_index=-1,
                        start_offset=line_start[i],
                        end_offset=line_start[i + 1],
                        metadata={"page_number": m.group(1)},
                    )
                )

        tokens = self._md.parse(text)

        # 预提取 heading 的 inline 内容
        heading_inline: dict[int, str] = {
            idx: tokens[idx + 1].content
            for idx, tok in enumerate(tokens)
            if tok.type == "heading_open" and idx + 1 < len(tokens)
        }

        # 预提取 image token：图片是 inline token（level > 0），
        # 需要单独提取为 IMAGE unit
        image_units: list[TextUnit] = []
        # 记录图片 token 的行范围，后续从 paragraph 中剥离
        image_line_ranges: list[tuple[int, int]] = []
        for tok in tokens:
            if tok.type != "image" or tok.map is None:
                continue
            img_start, img_end = tok.map
            image_line_ranges.append((img_start, img_end))
            img_text = "".join(lines[img_start:img_end]).strip()
            if not img_text:
                continue
            image_units.append(
                TextUnit(
                    unit_id=f"unit-img-{len(image_units)}",
                    text=img_text,
                    unit_type=UnitType.IMAGE,
                    unit_index=-1,
                    start_offset=line_start[img_start],
                    end_offset=line_start[img_end],
                    metadata={
                        "alt": tok.content,  # 图片 alt 文本，如 "Figure 1: 示意图"
                        "src": tok.attrGet("href") or "",
                    },
                )
            )

        # 判断某行是否属于图片
        def _is_image_line(line_no: int) -> bool:
            return any(s <= line_no < e for s, e in image_line_ranges)

        # 处理 block 级 token
        units: list[TextUnit] = []
        section_path: list[str] = []

        for idx, token in enumerate(tokens):
            if token.level != 0:
                continue
            if token.nesting not in (0, 1):
                continue
            if token.type not in _BLOCK_OPENERS:
                continue
            if token.map is None:
                continue

            start_line, end_line = token.map

            # 对于 paragraph，剥离图片行，只保留非图片文本
            if token.type == "paragraph_open" and image_line_ranges:
                non_img_lines = [
                    lines[i] for i in range(start_line, end_line)
                    if not _is_image_line(i)
                ]
                block_text = "".join(non_img_lines).strip()
                if not block_text:
                    # 整个 paragraph 都是图片，跳过（图片已作为 IMAGE unit）
                    continue
            else:
                block_text = "".join(lines[start_line:end_line]).strip()
                if not block_text:
                    continue

            unit_type = _TOKEN_TO_UNIT_TYPE[token.type]

            heading_title: str | None = None
            if unit_type == UnitType.HEADING:
                heading_title = heading_inline.get(idx)
                if heading_title:
                    section_path = [heading_title]

            start_offset = line_start[start_line]
            end_offset = line_start[end_line]

            units.append(
                TextUnit(
                    unit_id=f"unit-{len(units)}",
                    text=block_text,
                    unit_type=unit_type,
                    unit_index=len(units),
                    start_offset=start_offset,
                    end_offset=end_offset,
                    section_path=tuple(section_path),
                    metadata={
                        "block_index": len(units),
                        "block_type": unit_type,
                        **({"title": heading_title} if heading_title else {}),
                    },
                )
            )

        # 合并所有 units，按 start_offset 排序，统一重编号
        all_units = units + page_marker_units + image_units
        if not all_units:
            return (
                TextUnit(
                    unit_id="unit-0",
                    text=text,
                    unit_type=UnitType.UNKNOWN,
                    unit_index=0,
                    start_offset=0,
                    end_offset=len(text),
                ),
            )

        all_units.sort(key=lambda u: u.start_offset)
        result: list[TextUnit] = []
        for i, unit in enumerate(all_units):
            result.append(replace(unit, unit_id=f"unit-{i}", unit_index=i))

        return tuple(result)
