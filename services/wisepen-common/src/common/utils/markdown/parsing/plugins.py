from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore


def standalone_figure_plugin(md: MarkdownIt) -> None:
    """将独占段落的单张图片提升为 figure token。"""

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
