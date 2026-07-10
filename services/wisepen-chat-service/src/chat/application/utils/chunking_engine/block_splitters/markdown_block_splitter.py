from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..models import ChunkDocument, TextBlock, BlockKind

# 统一页码标记格式：<!-- page N -->
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")
# PDF/Docling 导出的表题常是普通段落，如 "· Table 1: ..."，后面才跟 Markdown 表格。
# 这里先识别表题编号，后续把紧邻的表题段落和表格块合并为同一个 TABLE block。
_TABLE_CAPTION_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*(?:Table|表格|表)\s+(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

_TOKEN_TO_BLOCK_KIND: dict[str, BlockKind] = {
    "heading_open": BlockKind.HEADING,
    "fence": BlockKind.CODE,
    "code_block": BlockKind.CODE,
    "table_open": BlockKind.TABLE,
    "blockquote_open": BlockKind.QUOTE,
    "bullet_list_open": BlockKind.LIST,
    "ordered_list_open": BlockKind.LIST,
    "paragraph_open": BlockKind.PARAGRAPH,
    "math_block": BlockKind.FORMULA,
}

_BLOCK_OPENERS = frozenset((*_TOKEN_TO_BLOCK_KIND, "html_block"))


class MarkdownBlockSplitter:
    """按 Markdown 结构切分文档，产出结构化 TextBlock。

    识别的 block 类型：HEADING / PARAGRAPH / TABLE / CODE / FORMULA / LIST / QUOTE / IMAGE / PAGE_MARKER。

    图片处理：markdown-it 将图片解析为 inline token（在 paragraph 内部），
    本 block_splitter 将其提取为独立的 IMAGE block，alt 文本存入 metadata["alt"]。
    """

    __slots__ = ("name", "_md")

    def __init__(self) -> None:
        self.name = "markdown_block_splitter"
        self._md = MarkdownIt("commonmark").enable("table").use(dollarmath_plugin)

    def split(self, *, document: ChunkDocument) -> tuple[TextBlock, ...]:
        text = document.text
        if not text:
            return ()

        # 预计算每行的起始 offset
        lines = text.splitlines(keepends=True)
        line_start = [0] * (len(lines) + 1)
        for i, line in enumerate(lines):
            line_start[i + 1] = line_start[i] + len(line)

        # 先扫描页码标记行（<!-- page N -->），markdown-it 不会识别为独立 token
        page_marker_blocks: list[TextBlock] = []
        for i, line in enumerate(lines):
            m = _PAGE_MARKER_RE.match(line)
            if m:
                page_marker_blocks.append(
                    TextBlock(
                        block_id=f"block-pm-{len(page_marker_blocks)}",
                        text=line.strip(),
                        block_kind=BlockKind.PAGE_MARKER,
                        block_index=-1,
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
        # 需要单独提取为 IMAGE block
        image_blocks: list[TextBlock] = []
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
            image_blocks.append(
                TextBlock(
                    block_id=f"block-img-{len(image_blocks)}",
                    text=img_text,
                    block_kind=BlockKind.IMAGE,
                    block_index=-1,
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
        blocks: list[TextBlock] = []
        heading_stack: list[tuple[int, str]] = []

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
                    # 整个 paragraph 都是图片，跳过（图片已作为 IMAGE block）
                    continue
            else:
                block_text = "".join(lines[start_line:end_line]).strip()
                if not block_text:
                    continue

            # markdown-it 的 table rule 覆盖 pipe table；原始 HTML 表格会落到 html_block。
            # 非 table 的 HTML 块不是当前结构语义，直接跳过，避免误标为普通段落。
            if token.type == "html_block":
                if not block_text.lstrip().lower().startswith("<table"):
                    continue
                block_kind = BlockKind.TABLE
            else:
                block_kind = _TOKEN_TO_BLOCK_KIND[token.type]

            heading_title: str | None = None
            section_path = tuple(title for _, title in heading_stack)
            if block_kind == BlockKind.HEADING:
                heading_title = heading_inline.get(idx)
                if heading_title:
                    heading_level = int(token.tag[1])
                    heading_stack = [
                        (level, title)
                        for level, title in heading_stack
                        if level < heading_level
                    ]
                    heading_stack.append((heading_level, heading_title))
                    section_path = tuple(title for _, title in heading_stack)

            start_offset = line_start[start_line]
            end_offset = line_start[end_line]

            blocks.append(
                TextBlock(
                    block_id=f"block-{len(blocks)}",
                    text=block_text,
                    block_kind=block_kind,
                    block_index=len(blocks),
                    start_offset=start_offset,
                    end_offset=end_offset,
                    section_path=section_path,
                    metadata={
                        "block_index": len(blocks),
                        "block_type": block_kind,
                        **({"title": heading_title} if heading_title else {}),
                    },
                )
            )

        blocks = _merge_captioned_tables(blocks, text)

        # 合并所有 blocks，按 start_offset 排序，统一重编号
        all_blocks = blocks + page_marker_blocks + image_blocks
        if not all_blocks:
            return (
                TextBlock(
                    block_id="block-0",
                    text=text,
                    block_kind=BlockKind.UNKNOWN,
                    block_index=0,
                    start_offset=0,
                    end_offset=len(text),
                ),
            )

        all_blocks.sort(key=lambda block: block.start_offset)
        result: list[TextBlock] = []
        for i, block in enumerate(all_blocks):
            result.append(replace(block, block_id=f"block-{i}", block_index=i))

        return tuple(result)


def _merge_captioned_tables(blocks: list[TextBlock], text: str) -> list[TextBlock]:
    """将“表题段落 + 紧邻表格”收敛为一个 TABLE block。"""
    merged: list[TextBlock] = []
    i = 0

    while i < len(blocks):
        caption = blocks[i]
        table = blocks[i + 1] if i + 1 < len(blocks) else None
        anchor_label = _table_caption_label(caption.text)
        # 表题和表格必须在原文中只隔空白；如果中间有正文，就不能把它吞进表格。
        if (
            table is None
            or caption.block_kind != BlockKind.PARAGRAPH
            or table.block_kind != BlockKind.TABLE
            or anchor_label is None
            or caption.end_offset is None
            or table.start_offset is None
            or text[caption.end_offset:table.start_offset].strip()
        ):
            merged.append(caption)
            i += 1
            continue

        start = caption.start_offset
        end = table.end_offset
        merged.append(
            replace(
                table,
                text=(
                    text[start:end].strip()
                    if start is not None and end is not None
                    else f"{caption.text}\n\n{table.text}".strip()
                ),
                start_offset=start,
                end_offset=end,
                section_path=table.section_path or caption.section_path,
                metadata={
                    **table.metadata,
                    "block_type": BlockKind.TABLE,
                    "caption": caption.text,
                    "anchor_label": anchor_label,
                },
            )
        )
        i += 2

    return merged


def _table_caption_label(text: str) -> str | None:
    first_line = text.split("\n", 1)[0].strip()
    match = _TABLE_CAPTION_RE.match(first_line)
    if match:
        return f"Table {match.group(1)}"
    return None
