from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..models import BlockKind, TextBlock

PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")
TABLE_CAPTION_RE = re.compile(
    r"^(?:[·•]\s*|[-*+]\s+)?[*_`~\s]*(?:Table|表格|表)\s+(\d+(?:\.\d+)*)",
    re.IGNORECASE,
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
        line_offsets = [0]
        page_markers: list[TextBlock] = []
        # 构建行 offset 时同步提取页码，避免对所有行做第二轮扫描。
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
        blocks.extend(page_markers)
        blocks.sort(
            key=lambda block: (
                block.start_offset if block.start_offset is not None else -1,
                block.end_offset if block.end_offset is not None else -1,
            )
        )
        blocks = _merge_captioned_tables(blocks, text)

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
        headings: list[tuple[int, str]] = []

        for index, token in enumerate(tokens):
            if token.level != 0 or token.map is None:
                continue

            inline_token = (
                tokens[index + 1]
                if index + 1 < len(tokens) and tokens[index + 1].type == "inline"
                else None
            )
            image = (
                _image_only(inline_token) if token.type == "paragraph_open" else None
            )
            kind = _token_kind(token, image)
            if kind is None:
                continue

            start_line, end_line = token.map
            block_text = "".join(lines[start_line:end_line]).strip()
            if not block_text or PAGE_MARKER_RE.fullmatch(block_text):
                continue

            metadata: dict[str, object] = {}
            section_path = tuple(title for _, title in headings)
            if kind == BlockKind.HEADING:
                title = inline_token.content.strip() if inline_token else block_text
                level = int(token.tag[1])
                headings = [
                    (depth, value) for depth, value in headings if depth < level
                ]
                headings.append((level, title))
                section_path = tuple(value for _, value in headings)
                metadata["title"] = title
            elif image is not None:
                metadata.update(
                    {
                        "alt": image.content,
                        "src": image.attrGet("src") or "",
                    }
                )

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


def _token_kind(token: Token, image: Token | None) -> BlockKind | None:
    """将顶层 token 收敛为模块支持的结构类型。"""
    if token.type == "html_block":
        return (
            BlockKind.TABLE
            if token.content.lstrip().lower().startswith("<table")
            else None
        )
    if token.type == "paragraph_open":
        return BlockKind.IMAGE if image is not None else BlockKind.PARAGRAPH
    return _TOKEN_KINDS.get(token.type)


def _image_only(inline_token: Token | None) -> Token | None:
    """如果段落只包含图片和空白，一次遍历返回第一张图片。"""
    if inline_token is None:
        return None

    image: Token | None = None
    for child in inline_token.children or ():
        if child.type == "softbreak" or (
            child.type == "text" and not child.content.strip()
        ):
            continue
        if child.type != "image":
            return None
        image = image or child
    return image


def _merge_captioned_tables(
    blocks: list[TextBlock],
    text: str,
) -> list[TextBlock]:
    """将紧邻的普通表题段落与表格合为一个原子 TABLE block。

    page marker 会出现在两者之间，因此天然阻止跨页表题合并。
    """
    merged: list[TextBlock] = []
    index = 0

    while index < len(blocks):
        caption = blocks[index]
        if index + 1 < len(blocks):
            table = blocks[index + 1]
            match = TABLE_CAPTION_RE.match(caption.text.partition("\n")[0].strip())
            if (
                caption.block_kind == BlockKind.PARAGRAPH
                and table.block_kind == BlockKind.TABLE
                and match is not None
                and caption.end_offset is not None
                and table.start_offset is not None
                and not text[caption.end_offset : table.start_offset].strip()
            ):
                start_offset = caption.start_offset
                end_offset = table.end_offset
                merged.append(
                    replace(
                        table,
                        text=(
                            text[start_offset:end_offset].strip()
                            if start_offset is not None and end_offset is not None
                            else f"{caption.text}\n\n{table.text}"
                        ),
                        start_offset=start_offset,
                        section_path=table.section_path or caption.section_path,
                        metadata={
                            **table.metadata,
                            "caption": caption.text,
                            "anchor_label": f"Table {match.group(1)}",
                        },
                    )
                )
                index += 2
                continue

        merged.append(caption)
        index += 1

    return merged
