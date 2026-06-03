import asyncio
from typing import List, Optional, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.application.tools.document.services.document_export.models import ExportRequest
from chat.application.tools.document.services.document_export.renderers.base import (
    DocumentRenderer,
)


class TxtRenderer(DocumentRenderer):
    """
    Markdown -> TXT 渲染器。

    - 使用 markdown-it 解析 Markdown token。
    - 丢弃 Markdown 样式，只保留可读纯文本。
    - 表格降级为 ` | ` 分隔文本。
    - 代码块保留原始换行。
    """

    def __init__(self) -> None:
        """初始化 TxtRenderer，创建 MarkdownIt 解析器实例并启用表格与删除线。"""
        self.md = MarkdownIt("commonmark", {"html": False, "linkify": True})
        self.md.enable("table")
        self.md.enable("strikethrough")

    @property
    def target_format(self) -> ExportFormat:
        """返回目标格式 `ExportFormat.TXT`。"""
        return ExportFormat.TXT

    async def render(self, request: ExportRequest) -> None:
        """将 Markdown 渲染为纯文本文件。"""
        # Markdown 先解析成 token，再转换成纯文本行。
        tokens = self.md.parse(request.markdown)
        lines = self._tokens_to_lines(tokens)

        # 压缩连续空行，并保证最终文件至少有一个换行。
        text = "\n".join(self._compact_lines(lines)).strip()
        text = text + "\n" if text else "\n"

        await asyncio.to_thread(
            request.output_path.write_text,
            text,
            encoding="utf-8",
            newline="\n",
        )

    def _tokens_to_lines(self, tokens: Sequence[Token]) -> List[str]:
        """将 Markdown token 序列转换为纯文本行，处理表格、代码块、列表等。"""
        lines: List[str] = []

        # 表格状态。
        in_table = False
        table_rows: List[List[str]] = []
        current_row: Optional[List[str]] = None
        current_cell_parts: Optional[List[str]] = None

        # 列表状态。当前只做纯文本降级，不保留复杂嵌套层级。
        pending_list_prefix: Optional[str] = None
        ordered_list_index: Optional[int] = None

        for token in tokens:
            if token.type == "table_open":
                in_table = True
                table_rows = []
                continue

            if token.type == "table_close":
                # TXT 中表格降级为简单的竖线分隔行。
                for row in table_rows:
                    if row:
                        lines.append(" | ".join(row))
                lines.append("")
                in_table = False
                continue

            if token.type == "tr_open":
                current_row = []
                continue

            if token.type == "tr_close":
                if current_row is not None:
                    table_rows.append(current_row)
                current_row = None
                continue

            if token.type in ("th_open", "td_open"):
                current_cell_parts = []
                continue

            if token.type in ("th_close", "td_close"):
                if current_row is not None and current_cell_parts is not None:
                    current_row.append(" ".join("".join(current_cell_parts).split()))
                current_cell_parts = None
                continue

            if token.type in ("fence", "code_block"):
                # 代码块保留原始换行，只去掉尾部多余换行。
                lines.extend(token.content.rstrip("\n").split("\n"))
                lines.append("")
                continue

            if token.type == "bullet_list_open":
                ordered_list_index = None
                continue

            if token.type == "ordered_list_open":
                start = token.attrGet("start")
                ordered_list_index = int(start) if start and start.isdigit() else 1
                continue

            if token.type == "list_item_open":
                if ordered_list_index is None:
                    pending_list_prefix = "- "
                else:
                    pending_list_prefix = f"{ordered_list_index}. "
                    ordered_list_index += 1
                continue

            if token.type in (
                "bullet_list_close",
                "ordered_list_close",
                "blockquote_close",
            ):
                lines.append("")
                if token.type == "ordered_list_close":
                    ordered_list_index = None
                continue

            if token.type == "inline":
                text = self._inline_tokens_to_text(token.children or [])

                if in_table and current_cell_parts is not None:
                    current_cell_parts.append(text)
                    continue

                if text.strip():
                    if pending_list_prefix is not None:
                        lines.append(pending_list_prefix + text.strip())
                        pending_list_prefix = None
                    else:
                        lines.extend(text.split("\n"))

                continue

            if token.type in ("paragraph_close", "heading_close"):
                lines.append("")

        return lines

    def _inline_tokens_to_text(self, tokens: Sequence[Token]) -> str:
        """将 Markdown 文本转换为纯文本，去除语法标记但保留换行和段落结构。"""
        parts: List[str] = []

        for token in tokens:
            if token.type in ("text", "code_inline"):
                parts.append(token.content)
            elif token.type in ("softbreak", "hardbreak"):
                parts.append("\n")
            elif token.type == "image":
                # 图片在 TXT 中降级为 alt 文本；没有 alt 时使用 token.content。
                image_text = self._inline_tokens_to_text(token.children or [])
                parts.append(image_text or token.content)
            elif token.children:
                # strong / em / link 等样式 token 只保留内部文本。
                parts.append(self._inline_tokens_to_text(token.children))

        return "".join(parts)

    def _compact_lines(self, lines: Sequence[str]) -> List[str]:
        """压缩连续空行为单个空行，去除文末多余空行。"""
        compacted: List[str] = []
        previous_blank = True

        for line in lines:
            cleaned = line.rstrip()
            is_blank = cleaned == ""

            # 压缩连续空行。
            if is_blank and previous_blank:
                continue

            compacted.append(cleaned)
            previous_blank = is_blank

        # 去掉文末空行，由 render 统一补一个最终换行。
        while compacted and compacted[-1] == "":
            compacted.pop()

        return compacted
