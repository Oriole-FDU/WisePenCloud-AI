from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_core import StateCore

_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(?P<label>\d+)\s*-->[ \t]*")


def page_marker_plugin(md: MarkdownIt) -> None:
    """将独占一行的页标注释解析为块级 token。

    页标进入结构模型用于 Page 定位，但 chunker 会把它从正文装箱中排除，
    因而不会把一个语义 Section 强制切成两段。
    """

    def page_marker_rule(
        state: StateBlock,
        start_line: int,
        end_line: int,
        silent: bool,
    ) -> bool:
        if state.sCount[start_line] - state.blkIndent >= 4:
            return False

        start = state.bMarks[start_line] + state.tShift[start_line]
        end = state.eMarks[start_line]
        match = _PAGE_MARKER_RE.fullmatch(state.src[start:end])
        if match is None:
            return False
        if silent:
            return True

        token = state.push("page_marker", "", 0)
        token.block = True
        token.map = [start_line, start_line + 1]
        token.meta = {"page_label": match.group("label")}
        state.line = start_line + 1
        return True

    md.block.ruler.before(
        "html_block",
        "page_marker",
        page_marker_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )


def standalone_figure_plugin(md: MarkdownIt) -> None:
    """将独占段落的单张图片提升为 figure token。

    只有图片单独占据一个段落时才提升，避免改变包含文字或多元素段落的原有语义边界。
    """

    def promote_standalone_images(state: StateCore) -> None:
        tokens = state.tokens
        for index in range(len(tokens) - 2):
            opening, inline, closing = tokens[index : index + 3]
            if (
                opening.type != "paragraph_open"
                or inline.type != "inline"
                or closing.type != "paragraph_close"
                or inline.children is None
                or len(inline.children) != 1
                or inline.children[0].type != "image"
            ):
                continue

            opening.type = "figure_open"
            opening.tag = "figure"
            closing.type = "figure_close"
            closing.tag = "figure"

    md.core.ruler.after("inline", "standalone_figure", promote_standalone_images)
