from __future__ import annotations

from dataclasses import replace

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ..models import ChunkDocument

_md = MarkdownIt().use(dollarmath_plugin)


class MarkdownPreProcessor:
    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "markdown_preprocessor"

    def process(self, *, document: ChunkDocument) -> ChunkDocument:
        if not document.text:
            return document
        return replace(document, text=_inject_heading_paths(document.text))


def _inject_heading_paths(text: str) -> str:
    lines = text.splitlines(keepends=True)
    tokens = _md.parse(text)

    sections: list[str] = []
    heading_stack: list[tuple[int, str]] = []  # (h_level, title)

    for i, token in enumerate(tokens):
        # 只取文档顶层（level=0），跳过所有 close token
        if token.level != 0 or token.map is None or token.nesting == -1:
            continue

        if token.type == "heading_open":
            h_level = int(token.tag[1])          # "h2" → 2
            title = tokens[i + 1].content if i + 1 < len(tokens) else ""
            heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < h_level]
            heading_stack.append((h_level, title))
            continue

        start_line, end_line = token.map
        block_text = "".join(lines[start_line:end_line]).strip()
        if not block_text:
            continue

        if heading_stack:
            path = " > ".join(t for _, t in heading_stack)
            sections.append(f"Section: {path}\n{block_text}")
        else:
            sections.append(block_text)

    return "\n\n".join(sections) if sections else text.strip()