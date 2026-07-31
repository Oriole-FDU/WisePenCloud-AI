from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..models import BlockKind, TextBlock
from .plugins import page_marker_plugin, standalone_figure_plugin


# 匹配 "Table N: 标题" / "图 1.2 标题" 这类编号型题注。
# 兼容：列表前缀 (`- ` / `· `)、强调符号、中英文标点
NUMBERED_LABEL_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*"
    r"(?:(?P<table_label>Table|表格|表)|(?P<figure_label>Figure|Fig\.?|图))"
    r"\s*(?P<number>\d+(?:\.\d+)*)\s*[-:：.．、]\s*"
    r"(?P<title>\S(?:.*\S)?)[*_`~\s]*$",
    re.IGNORECASE | re.DOTALL,
)

# 匹配公式题注："Equation 1" / "Eq. (1.2)" / "公式 3"
FORMULA_LABEL_RE = re.compile(
    r"(?:Equation|Eq\.?|公式)\s+\(?(?P<number>\d+(?:\.\d+)*)\)?",
    re.IGNORECASE,
)

# markdown-it token 类型 → 模块 BlockKind 的默认映射。
# html_block / paragraph_open 走特殊分支，不在此表。
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
}


class MarkdownParser:
    """将 Markdown parser 的块级 token 映射为带原文位置的结构块。

    只消费顶层 token，列表和引用分别保持为一个整体，避免内部 paragraph
    再次产出造成文本重叠。插件先补充页标和独占图片结构，再统一映射 offset。
    """

    __slots__ = ("_parser",)

    def __init__(self) -> None:
        self._parser = (
            MarkdownIt("commonmark")
            .enable("table")
            .use(page_marker_plugin)
            .use(standalone_figure_plugin)
            .use(dollarmath_plugin)
        )

    def parse(self, text: str) -> tuple[TextBlock, ...]:
        """完整解析流程：tokens → 顶层块 → 合并编号题注 → 投影页码。"""
        if not text:
            return ()

        # 预计算每行的起始偏移，便于 token.map 直接还原绝对 offset
        lines = text.splitlines(keepends=True)
        line_offsets = [0]
        for line in lines:
            line_offsets.append(line_offsets[-1] + len(line))

        tokens = self._parser.parse(text)
        blocks = self._parse_tokens(tokens, lines, line_offsets)

        # 顺序敏感：先合并编号题注拿到完整文本范围，再让页标覆盖到最终块上
        blocks = _associate_numbered_labels(blocks, text)
        blocks = _attach_page_labels(blocks)

        # 兜底：解析不到任何结构块时，把整篇文本作为单个 UNKNOWN
        if not blocks:
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

        return tuple(
            replace(
                block,
                block_id=f"block-{index}",
                block_index=index,
            )
            for index, block in enumerate(blocks)
        )

    def _parse_tokens(
        self,
        tokens: list[Token],
        lines: list[str],
        line_offsets: list[int],
    ) -> list[TextBlock]:
        """解析顶层 token，并维护当前标题栈形成完整 section_path。

        标题栈保存 (heading_level, title)。遇到同级或更浅的标题时，
        弹出同级及其下级标题，使 section_path 始终指向当前完整层级。
        """
        blocks: list[TextBlock] = []
        headings: list[tuple[int, str]] = []

        for index, token in enumerate(tokens):
            # 只关心顶层块（level==0 且带行映射），列表/引用内部的子 token 一律跳过
            if token.level != 0 or token.map is None:
                continue

            # heading_open 紧跟的 inline token 是纯文本标题，可用于提取 title
            inline_token = (
                tokens[index + 1]
                if index + 1 < len(tokens) and tokens[index + 1].type == "inline"
                else None
            )
            kind = _token_kind(token)
            if kind is None:
                continue

            start_line, end_line = token.map
            block_text = "".join(lines[start_line:end_line])
            if kind is BlockKind.PAGE_MARKER:
                block_text = block_text.strip()
            if not block_text.strip():
                continue

            # 构造 metadata / section_path，并按 token 类型写入专属信息
            metadata: dict[str, object] = {}
            section_path = (
                ()
                if kind is BlockKind.PAGE_MARKER
                else tuple(title for _, title in headings)
            )

            if kind is BlockKind.PAGE_MARKER:
                metadata["page_label"] = token.meta["page_label"]
            elif kind is BlockKind.FORMULA:
                formula_match = FORMULA_LABEL_RE.search(block_text)
                if formula_match is not None:
                    metadata["anchor_label"] = (
                        f"Equation {formula_match.group('number')}"
                    )
            elif kind is BlockKind.HEADING:
                title = inline_token.content.strip() if inline_token else block_text
                level = int(token.tag[1])
                # 弹出 >= 当前层级的标题，模拟文档大纲
                # 例如遇到 H3 时弹出栈中已有的 H3/H4/...，保留 H1/H2
                headings = [
                    (depth, value) for depth, value in headings if depth < level
                ]
                headings.append((level, title))
                section_path = tuple(value for _, value in headings)
                metadata["title"] = title
                metadata["heading_level"] = level

            blocks.append(
                TextBlock(
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


# --- Token 归一化与编号锚点提取 ---


def _token_kind(token: Token) -> BlockKind | None:
    """将 markdown-it 的 token 类型收敛为模块支持的 BlockKind。

    html_block 需要特殊处理：只识别 MinerU 输出的 table，其余返回 None
    走忽略分支，避免把整段 HTML 误当成普通段落。
    """
    if token.type == "html_block":
        html = token.content.lstrip().lower()
        if html.startswith("<table"):
            return BlockKind.TABLE
        return None
    if token.type == "paragraph_open":
        return BlockKind.PARAGRAPH
    return _TOKEN_KINDS.get(token.type)


def _numbered_anchor(text: str) -> tuple[BlockKind, str] | None:
    """识别严格编号题注，返回 (BlockKind, "Table/Figure N") 或 None。"""
    match = NUMBERED_LABEL_RE.fullmatch(text.strip())
    if match is None:
        return None
    number = match.group("number")
    if match.group("table_label") is not None:
        return BlockKind.TABLE, f"Table {number}"
    return BlockKind.FIGURE, f"Figure {number}"


# --- 后处理：合并题注与投影页码 ---


def _associate_numbered_labels(
    blocks: list[TextBlock],
    text: str,
) -> list[TextBlock]:
    """将上置或下置的编号题注与相邻表格/图片合并为一个 chunk。

    普通 Markdown 会把表题和图题解析为 paragraph。这里只识别严格编号标签，
    目的是保留主体的完整 chunk 范围并生成定位锚点，不建立独立的 caption 模型。
    """
    associated: list[TextBlock] = []
    index = 0

    while index < len(blocks):
        first = blocks[index]
        if index + 1 < len(blocks):
            second = blocks[index + 1]
            # caption 可以位于目标块上方或下方，所以分别尝试两种顺序
            caption, target = (
                (first, second)
                if first.block_kind == BlockKind.PARAGRAPH
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
                # 两个 block 之间不能有非空白内容，防止误合并无关段落
                and first.end_offset is not None
                and second.start_offset is not None
                and not text[first.end_offset : second.start_offset].strip()
            ):
                start_offset = first.start_offset
                end_offset = second.end_offset
                associated.append(
                    replace(
                        target,
                        text=(
                            text[start_offset:end_offset]
                            if start_offset is not None and end_offset is not None
                            else f"{first.text}\n\n{second.text}"
                        ),
                        start_offset=start_offset,
                        end_offset=end_offset,
                        section_path=target.section_path or caption.section_path,
                        metadata={
                            **target.metadata,
                            "anchor_label": anchor[1],
                        },
                    )
                )
                index += 2
                continue

        associated.append(first)
        index += 1

    return associated


def _attach_page_labels(blocks: list[TextBlock]) -> list[TextBlock]:
    """把页标记投影到后续结构块。

    遍历过程中维护 active_page_label，遇到 PAGE_MARKER 就更新，
    后续所有结构块都携带该页码直到下一个 PAGE_MARKER。页标记本身保留。
    """
    active_page_label: str | None = None
    labeled: list[TextBlock] = []
    for block in blocks:
        if block.block_kind == BlockKind.PAGE_MARKER:
            active_page_label = str(block.metadata["page_label"])
            labeled.append(block)
            continue

        metadata = dict(block.metadata)
        if active_page_label is not None:
            metadata["page_label"] = active_page_label
        labeled.append(replace(block, metadata=metadata))

    return labeled
