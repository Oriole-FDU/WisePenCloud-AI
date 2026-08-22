from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from .models import BlockKind, DocumentBlock
from .plugins import page_marker_plugin, standalone_figure_plugin

NUMBERED_LABEL_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*"
    r"(?:(?P<table_label>Table|表格|表)|(?P<figure_label>Figure|Fig\.?|图))"
    r"\s*(?P<number>\d+(?:\.\d+)*)\s*[-:：.．、]\s*"
    r"(?P<title>\S(?:.*\S)?)[*_`~\s]*$",
    re.IGNORECASE | re.DOTALL,
)
FORMULA_LABEL_RE = re.compile(
    r"(?:Equation|Eq\.?|公式)\s+\(?(?P<number>\d+(?:\.\d+)*)\)?",
    re.IGNORECASE,
)
_TOKEN_KINDS: dict[str, BlockKind] = {
    "heading_open": BlockKind.HEADING,
    "figure_open": BlockKind.FIGURE,
    "fence": BlockKind.CODE,
    "code_block": BlockKind.CODE,
    "table_open": BlockKind.TABLE,
    "blockquote_open": BlockKind.QUOTE,
    "bullet_list_open": BlockKind.LIST,
    "ordered_list_open": BlockKind.LIST,
    "math_block": BlockKind.FORMULA,
    "math_block_label": BlockKind.FORMULA,
    "page_marker": BlockKind.PAGE_MARKER,
    "paragraph_open": BlockKind.PARAGRAPH
}


class DocumentParser:
    """把 Markdown-compatible 文本解析为带原文位置的块级结构。"""

    def __init__(self) -> None:
        # 先让 markdown-it-py 负责语法边界，再把扩展 token 投影为 Common 的统一 block。
        self._parser = (
            MarkdownIt("commonmark")
            .disable("lheading")    # 禁用 Setext 标题，避免与标题栈冲突
            .enable("table")
            .use(page_marker_plugin)
            .use(standalone_figure_plugin)
            .use(dollarmath_plugin)
        )

    def parse(self, text: str) -> tuple[DocumentBlock, ...]:
        if not text:
            return ()

        lines = text.splitlines(keepends=True)  # 保留换行符，避免累加漂移
        # token.map 使用行号；这里转换为 Python 字符偏移，供所有下游结构复用。
        line_offsets = [0]
        for line in lines:
            line_offsets.append(line_offsets[-1] + len(line))

        blocks = self._parse_tokens(self._parser.parse(text), lines, line_offsets)
        # 题注合并会改变主体范围，页码必须在最终 block 上投影。
        blocks = _associate_numbered_labels(blocks, text)
        blocks = _attach_page_labels(blocks)
        if not blocks:
            return (
                DocumentBlock(
                    block_id="block-0",
                    text=text,
                    block_kind=BlockKind.UNKNOWN,
                    block_index=0,
                    start_offset=0,
                    end_offset=len(text),
                ),
            )

        return tuple(
            replace(block, block_id=f"block-{index}", block_index=index)
            for index, block in enumerate(blocks)
        )

    def _parse_tokens(
            self,
            tokens: list[Token],
            lines: list[str],
            line_offsets: list[int],
        ) -> list[DocumentBlock]:
            """只消费顶层 token，并维护标题栈形成完整 section_path。"""
            blocks: list[DocumentBlock] = []
            headings: list[tuple[int, str]] = []  # 栈：[(level, title), ...]

            for index, token in enumerate(tokens):
                if token.level != 0 or token.map is None:
                    continue

                kind = _token_kind(token)
                if kind is None:
                    continue

                start_line, end_line = token.map
                block_text = "".join(lines[start_line:end_line])
                if kind is BlockKind.PAGE_MARKER:
                    block_text = block_text.strip()
                if not block_text.strip():
                    continue

                metadata: dict[str, object] = {}

                if kind is BlockKind.HEADING:
                    inline = (
                        tokens[index + 1]
                        if index + 1 < len(tokens) and tokens[index + 1].type == "inline"
                        else None
                    )
                    title = inline.content.strip() if inline else block_text.strip()
                    level = int(token.tag[1])

                    # 弹出所有 >= 当前层级的同级或子标题
                    while headings and headings[-1][0] >= level:
                        headings.pop()
                    headings.append((level, title))

                    metadata["title"] = title
                    metadata["heading_level"] = level

                elif kind is BlockKind.PAGE_MARKER:
                    # 页标不属于任何 Section，只存页码元数据
                    metadata["page_label"] = token.meta["page_label"]

                elif kind is BlockKind.FORMULA:
                    formula_match = FORMULA_LABEL_RE.search(block_text)
                    if formula_match is not None:
                        metadata["anchor_label"] = f"Equation {formula_match.group('number')}"

                # 统一冻结 section_path 并构造 DocumentBlock
                section_path = () if kind is BlockKind.PAGE_MARKER else tuple(t for _, t in headings)

                blocks.append(
                    DocumentBlock(
                        block_id=f"block-{len(blocks)}",
                        text=block_text,
                        block_kind=kind,
                        block_index=len(blocks),
                        start_offset=line_offsets[start_line],
                        end_offset=line_offsets[end_line],
                        section_path=section_path,
                        metadata=metadata,
                    )
                )

            return blocks


def _token_kind(token: Token) -> BlockKind | None:
    # 仅兼容 html 表格，其他html块丢弃
    if token.type == "html_block":
        return (
            BlockKind.TABLE
            if token.content.lstrip().lower().startswith("<table")
            else None
        )
    return _TOKEN_KINDS.get(token.type)


def _numbered_anchor(text: str) -> tuple[BlockKind, str] | None:
    # 识别标准题注文字，常见于学术论文
    match = NUMBERED_LABEL_RE.fullmatch(text.strip())
    if match is None:
        return None
    number = match.group("number")
    if match.group("table_label") is not None:
        return BlockKind.TABLE, f"Table {number}"
    return BlockKind.FIGURE, f"Figure {number}"


def _associate_numbered_labels(
    blocks: list[DocumentBlock],
    text: str,
) -> list[DocumentBlock]:
    """合并相邻编号题注和主体，使 anchor 保持单一精确范围。"""
    associated: list[DocumentBlock] = []
    index = 0
    while index < len(blocks):
        first = blocks[index]
        if index + 1 < len(blocks):
            second = blocks[index + 1]
            # 兼容题注在上和在下两种情况
            caption, target = (
                (first, second)
                if first.block_kind is BlockKind.PARAGRAPH
                else (second, first)
            )
            anchor = (
                _numbered_anchor(caption.text)
                if caption.block_kind is BlockKind.PARAGRAPH
                else None
            )
            if (
                anchor is not None
                and target.block_kind is anchor[0]
                and not text[first.end_offset : second.start_offset].strip()
            ):
                associated.append(
                    replace(
                        target,
                        text=text[first.start_offset : second.end_offset],
                        start_offset=first.start_offset,
                        end_offset=second.end_offset,
                        section_path=target.section_path or caption.section_path,
                        metadata={**target.metadata, "anchor_label": anchor[1]},
                    )
                )
                index += 2
                continue

        associated.append(first)
        index += 1
    return associated


def _attach_page_labels(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """将页标投影到后续结构块，直到出现下一个页标。"""
    active_page_label: str | None = None
    labeled: list[DocumentBlock] = []
    for block in blocks:
        if block.block_kind is BlockKind.PAGE_MARKER:
            # 遇到页标，更新当前页码
            active_page_label = str(block.metadata["page_label"])
            labeled.append(block)
            continue

        metadata = block.metadata
        if active_page_label is not None:
            metadata["page_label"] = active_page_label
        labeled.append(replace(block, metadata=metadata))   # 打上页码标记
    return labeled
