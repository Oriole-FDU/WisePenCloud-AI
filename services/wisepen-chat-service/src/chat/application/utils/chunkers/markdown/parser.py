from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..models import BlockKind, TextBlock

PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")

NUMBERED_CAPTION_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*"
    r"(?:(?P<table_label>Table|表格|表)|(?P<figure_label>Figure|Fig\.?|图))"
    r"\s*(?P<number>\d+(?:\.\d+)*)\s*[-:：.．、]\s*"
    r"(?P<title>\S(?:.*\S)?)[*_`~\s]*$",
    re.IGNORECASE | re.DOTALL,
)

HTML_FIGCAPTION_RE = re.compile(
    r"<figcaption\b[^>]*>(?P<caption>.*?)</figcaption\s*>",
    re.IGNORECASE | re.DOTALL,
)

_TOKEN_KINDS = {
    "heading_open": BlockKind.HEADING,
    "fence": BlockKind.CODE,
    "code_block": BlockKind.CODE,
    "table_open": BlockKind.TABLE,
    "blockquote_open": BlockKind.QUOTE,
    "bullet_list_open": BlockKind.LIST,
    "ordered_list_open": BlockKind.LIST,
    "math_block": BlockKind.FORMULA,
}


class MarkdownParser:
    """将 Markdown parser 的块级 token 映射为带原文位置的结构块。

    只消费顶层 token，列表和引用分别保持为一个整体，避免内部 paragraph
    再次产出造成文本重叠。页码注释不属于 Markdown token，随行 offset 扫描提取。
    """

    __slots__ = ("_parser",)

    def __init__(self) -> None:
        self._parser = MarkdownIt("commonmark").enable("table").use(dollarmath_plugin)

    def parse(self, text: str) -> tuple[TextBlock, ...]:
        if not text:
            return ()

        lines = text.splitlines(keepends=True)
        # line_offsets[i] = 第 i 行在原文中的起始偏移，line_offsets[-1] = 文本总长
        line_offsets = [0]
        page_markers: list[TextBlock] = []
        # markdown-it 不保留 HTML 注释，页标必须在构建行 offset 时单独提取。
        for line_index, line in enumerate(lines):
            start_offset = line_offsets[-1]
            end_offset = start_offset + len(line)
            line_offsets.append(end_offset)

            stripped = line.strip()
            match = PAGE_MARKER_RE.fullmatch(stripped)
            if match is not None:
                page_markers.append(
                    TextBlock(
                        block_id=f"page-marker-{len(page_markers)}",
                        text=stripped,
                        block_kind=BlockKind.PAGE_MARKER,
                        block_index=-1,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        metadata={"page_label": match.group(1)},
                    )
                )

        tokens = self._parser.parse(text)
        blocks = self._parse_tokens(tokens, lines, line_offsets)
        # 页标与结构块按 offset 合并排序，确保后续 merge/attach 逻辑基于位置顺序
        blocks.extend(page_markers)
        blocks.sort(
            key=lambda block: (
                block.start_offset if block.start_offset is not None else -1,
                block.end_offset if block.end_offset is not None else -1,
            )
        )
        # 后处理：关联编号 caption、投影页码到结构块
        blocks = _merge_numbered_captions(blocks, text)
        blocks = _attach_page_labels(blocks)

        # 空结果兜底：整篇文本作为单个 UNKNOWN block
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
        """解析顶层 token，并维护当前标题栈形成完整 section_path。"""
        blocks: list[TextBlock] = []
        # 标题栈：(heading_level, title)，遇到更深层级时弹出浅层，形成层级路径
        headings: list[tuple[int, str]] = []

        for index, token in enumerate(tokens):
            # 只处理顶层块级 token（level==0 且有行映射）
            if token.level != 0 or token.map is None:
                continue

            # heading_open 的下一个 token 通常是 inline 类型，包含标题纯文本
            inline_token = (
                tokens[index + 1]
                if index + 1 < len(tokens) and tokens[index + 1].type == "inline"
                else None
            )
            kind = _token_kind(token)
            if kind is None:
                continue
            if kind is BlockKind.PARAGRAPH and _is_figure_paragraph(inline_token):
                kind = BlockKind.FIGURE

            start_line, end_line = token.map
            block_text = "".join(lines[start_line:end_line])
            if not block_text.strip() or PAGE_MARKER_RE.fullmatch(block_text.strip()):
                continue

            metadata: dict[str, object] = {}
            section_path = tuple(title for _, title in headings)
            if kind is BlockKind.FIGURE:
                figure_caption = _inline_figure_caption(inline_token)
                if figure_caption is None:
                    figure_caption = _html_figure_caption(block_text)
                if figure_caption is not None:
                    metadata["caption"] = figure_caption
                    caption_match = NUMBERED_CAPTION_RE.fullmatch(
                        figure_caption.strip()
                    )
                    if (
                        caption_match is not None
                        and caption_match.group("figure_label") is not None
                    ):
                        metadata["anchor_label"] = (
                            f"Figure {caption_match.group('number')}"
                        )
            if kind == BlockKind.HEADING:
                title = inline_token.content.strip() if inline_token else block_text
                level = int(token.tag[1])
                # 弹出 >= 当前层级的标题，模拟文档大纲的层级关系
                # 例如遇到 H3 时弹出栈中已有的 H3/H4/H5...，保留 H1/H2
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


def _token_kind(token: Token) -> BlockKind | None:
    """将顶层 token 收敛为模块支持的结构类型。

    html_block 需要特殊处理：只识别 table、figure 和独立 img，其余忽略
    （因为 HTML 注释页标已被单独提取，此处忽略不会遗漏）。
    """
    if token.type == "html_block":
        html = token.content.lstrip().lower()
        if html.startswith("<table"):
            return BlockKind.TABLE
        if html.startswith(("<figure", "<img")):
            return BlockKind.FIGURE
        return None
    if token.type == "paragraph_open":
        return BlockKind.PARAGRAPH
    return _TOKEN_KINDS.get(token.type)


def _is_figure_paragraph(inline_token: Token | None) -> bool:
    """仅把独立图片或“图片 + 编号图题”段落识别为 figure。"""
    if inline_token is None or not inline_token.children:
        return False

    images = [child for child in inline_token.children if child.type == "image"]
    if len(images) != 1:
        return False

    surrounding_text = " ".join(
        child.content.strip()
        for child in inline_token.children
        if child.type == "text" and child.content.strip()
    )
    if not surrounding_text:
        return True

    caption_match = NUMBERED_CAPTION_RE.fullmatch(surrounding_text.strip())
    return (
        caption_match is not None
        and caption_match.group("figure_label") is not None
    )


def _inline_figure_caption(inline_token: Token | None) -> str | None:
    if inline_token is None:
        return None
    for child in inline_token.children or ():
        if child.type != "image":
            continue
        caption_match = NUMBERED_CAPTION_RE.fullmatch(child.content.strip())
        if (
            caption_match is not None
            and caption_match.group("figure_label") is not None
        ):
            return child.content.strip()
    return None


def _html_figure_caption(text: str) -> str | None:
    match = HTML_FIGCAPTION_RE.search(text)
    return match.group("caption").strip() if match is not None else None


def _merge_numbered_captions(
    blocks: list[TextBlock],
    text: str,
) -> list[TextBlock]:
    """将紧邻的编号表题/图题与对应结构块合并。

    转换后的 Markdown 会把 caption 和 table/figure 解析为两个 block。
    本函数只合并标签与目标类型一致的相邻 block，避免 caption 被孤立为 PARAGRAPH。

    page marker 会作为中间 block 阻断候选，因此不会跨页合并。
    """
    merged: list[TextBlock] = []
    index = 0

    while index < len(blocks):
        first = blocks[index]
        if index + 1 < len(blocks):
            second = blocks[index + 1]
            # caption 可以位于目标块上方或下方，所以需要尝试两种顺序
            caption, target = (
                (first, second)
                if first.block_kind == BlockKind.PARAGRAPH
                else (second, first)
            )
            caption_match = (
                NUMBERED_CAPTION_RE.fullmatch(caption.text.strip())
                if caption.block_kind is BlockKind.PARAGRAPH
                else None
            )
            anchor_prefix: str | None = None
            if caption_match is not None:
                if (
                    target.block_kind is BlockKind.TABLE
                    and caption_match.group("table_label") is not None
                ):
                    anchor_prefix = "Table"
                elif (
                    target.block_kind is BlockKind.FIGURE
                    and caption_match.group("figure_label") is not None
                ):
                    anchor_prefix = "Figure"
            if (
                anchor_prefix is not None
                # 两个 block 之间不能有非空白内容（防止误合并不相关的段落）
                and first.end_offset is not None
                and second.start_offset is not None
                and not text[first.end_offset : second.start_offset].strip()
            ):
                start_offset = first.start_offset
                end_offset = second.end_offset
                merged.append(
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
                            "caption": caption.text,
                            "anchor_label": (
                                f"{anchor_prefix} {caption_match.group('number')}"
                            ),
                        },
                    )
                )
                index += 2
                continue

        merged.append(first)
        index += 1

    return merged


def _attach_page_labels(blocks: list[TextBlock]) -> list[TextBlock]:
    """把页标记投影到后续结构块。

    按 offset 排序后遍历，每遇到一个 PAGE_MARKER 就更新 active_page_label，
    后续所有结构块都会携带该页码直到下一个 PAGE_MARKER。页标记本身不参与投影。
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
