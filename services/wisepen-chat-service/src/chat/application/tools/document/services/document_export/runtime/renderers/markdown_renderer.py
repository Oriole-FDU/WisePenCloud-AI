import asyncio
import re

from chat.application.tools.document.services.document_export.enums import ExportFormat, ExportSourceFormat
from chat.application.tools.document.services.document_export.runtime.models import ExportRequest
from chat.application.tools.document.services.document_export.runtime.renderers.base import (
    DocumentRenderer,
)

_ORDERED_LIST_RE = re.compile(r"^(\d+)([.)])\s+")
_THEMATIC_BREAK_RE = re.compile(r"^([-*_])(?:\s*\1){2,}\s*$")


class MarkdownRenderer(DocumentRenderer):
    """
    Markdown 中间表示渲染器 + Markdown 文件渲染器。

    - render_to_markdown: 将 source content 转成 canonical Markdown。
    - source_format=markdown: 保留 Markdown 结构，只规范换行。
    - source_format=plain_text: 保真转义，避免纯文本被误解析成 Markdown 语法。
    - render: target_format=markdown 时，将 canonical Markdown 写成 .md 文件。
    """

    @property
    def target_format(self) -> ExportFormat:
        """返回目标格式 `ExportFormat.MARKDOWN`。"""
        return ExportFormat.MARKDOWN

    async def render(self, request: ExportRequest) -> None:
        """将 canonical Markdown 写入 .md 文件。"""
        await asyncio.to_thread(
            request.output_path.write_text,
            self._ensure_trailing_newline(request.markdown),
            encoding="utf-8",
            newline="\n",
        )

    def render_to_markdown(
        self,
        *,
        content: str,
        source_format: ExportSourceFormat,
    ) -> str:
        """将源内容转为 canonical Markdown：markdown 源保留结构，plain_text 进行保真转义。"""
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")

        if source_format == ExportSourceFormat.MARKDOWN:
            return self._ensure_trailing_newline(normalized)

        if source_format == ExportSourceFormat.PLAIN_TEXT:
            return self._ensure_trailing_newline(
                self._plain_text_to_markdown(normalized)
            )

        raise ValueError(f"Unsupported source format: {source_format.value}")

    def _plain_text_to_markdown(self, text: str) -> str:
        """
        纯文本转 canonical Markdown。

        - 不推断标题、列表、表格、代码块。
        - 只做保真转义，避免后续 HTML/PDF/DOCX renderer 误解析。
        - 连续非空物理行用 Markdown hard break 保留换行显示。
        """
        lines = text.split("\n")
        rendered_lines = []

        for index, line in enumerate(lines):
            rendered_line = self._plain_text_line_to_markdown(line)

            if rendered_line and index + 1 < len(lines) and lines[index + 1] != "":
                rendered_line += "  "

            rendered_lines.append(rendered_line)

        return "\n".join(rendered_lines)

    def _plain_text_line_to_markdown(self, line: str) -> str:
        """将单行纯文本转为保真 Markdown 行，保留前导/尾部空格。"""
        if line == "":
            return ""

        expanded = line.expandtabs(4)
        content_start = len(expanded) - len(expanded.lstrip(" "))
        leading_spaces = expanded[:content_start]
        content = expanded[content_start:]

        if not content:
            return self._spaces_to_markdown(leading_spaces)

        content_body = content.rstrip(" ")
        trailing_spaces = content[len(content_body):]

        escaped_content = self._escape_inline_text(content_body)
        escaped_content = self._escape_block_start(
            raw_content=content_body,
            escaped_content=escaped_content,
        )

        return (
            self._spaces_to_markdown(leading_spaces)
            + escaped_content
            + self._spaces_to_markdown(trailing_spaces)
        )

    def _escape_inline_text(self, text: str) -> str:
        """
        转义纯文本中的内联 Markdown / HTML 风险字符。

        - `*`, `_`, `` ` ``: 防止 emphasis / code。
        - `[`, `]`, `|`: 防止链接和表格误解析。
        - `&`, `<`, `>`: 防止 HTML entity / tag 误解析。
        """
        return (
            text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("*", "\\*")
            .replace("_", "\\_")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("|", "\\|")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _escape_block_start(self, *, raw_content: str, escaped_content: str) -> str:
        """
        防止纯文本行首被识别为 Markdown 块级语法。
        """
        stripped = raw_content.lstrip()

        if stripped.startswith("#"):
            return "\\" + escaped_content

        if stripped.startswith(("- ", "+ ")):
            return "\\" + escaped_content

        if _THEMATIC_BREAK_RE.match(stripped):
            return "\\" + escaped_content

        match = _ORDERED_LIST_RE.match(stripped)
        if match:
            number, delimiter = match.groups()
            prefix_length = len(raw_content) - len(stripped)
            leading = raw_content[:prefix_length]
            rest = stripped[match.end():]

            escaped_rest = self._escape_inline_text(rest)
            return f"{leading}{number}\\{delimiter} {escaped_rest}"

        return escaped_content

    def _spaces_to_markdown(self, spaces: str) -> str:
        """
        用 HTML entity 保留纯文本里的显式空格。

        只用于前导空格、尾部空格和全空格行，避免整行变成缩进代码块，
        同时避免尾部空格被 Markdown 当成 hard break 语法吞掉。
        """
        if not spaces:
            return ""
        return "&nbsp;" * len(spaces)

    def _ensure_trailing_newline(self, value: str) -> str:
        """确保文本以换行符结尾。"""
        return value if value.endswith("\n") else value + "\n"
