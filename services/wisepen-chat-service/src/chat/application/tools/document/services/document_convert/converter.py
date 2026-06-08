import asyncio
from pathlib import Path
from typing import Dict, FrozenSet, Tuple

from markitdown import MarkItDown

from chat.application.tools.document.services.document_convert.enums import (
    ConvertSourceFormat,
)
from chat.application.tools.document.services.document_convert.errors import (
    DocumentConvertError,
    DocumentDecodeError,
    DocumentParseFailedError,
    EmptyParsedMarkdownError,
    UnsupportedDocumentRouteError,
)
from chat.application.tools.document.services.document_export.enums import ExportSourceFormat
from chat.application.tools.document.services.document_export.runtime.renderers.markdown_renderer import \
    MarkdownRenderer
from chat.application.tools.document.services.document_parse.errors import (
    DocumentParseError,
)
from chat.application.tools.document.services.document_parse.service import (
    DocumentParseService,
)

_TEXT_READ_BYTES_LIMIT = 20 * 1024 * 1024
_TEXT_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "gbk")


_SUFFIX_TO_SOURCE_FORMAT: Dict[str, ConvertSourceFormat] = {
    ".md": ConvertSourceFormat.MARKDOWN,
    ".markdown": ConvertSourceFormat.MARKDOWN,
    ".txt": ConvertSourceFormat.PLAIN_TEXT,
    ".html": ConvertSourceFormat.HTML,
    ".htm": ConvertSourceFormat.HTML,
    ".pdf": ConvertSourceFormat.PDF,
    ".docx": ConvertSourceFormat.DOCX,
    ".docm": ConvertSourceFormat.DOCM,
    ".pptx": ConvertSourceFormat.PPTX,
    ".pptm": ConvertSourceFormat.PPTM,
    ".epub": ConvertSourceFormat.EPUB,
    ".xlsx": ConvertSourceFormat.XLSX,
    ".xls": ConvertSourceFormat.XLS,
    ".xlsm": ConvertSourceFormat.XLSM,
    ".ods": ConvertSourceFormat.ODS,
}


_PARSE_SOURCE_FORMATS: FrozenSet[ConvertSourceFormat] = frozenset(
    {
        ConvertSourceFormat.PDF,
        ConvertSourceFormat.DOCX,
        ConvertSourceFormat.DOCM,
        ConvertSourceFormat.PPTX,
        ConvertSourceFormat.PPTM,
        ConvertSourceFormat.EPUB,
        ConvertSourceFormat.XLSX,
        ConvertSourceFormat.XLS,
        ConvertSourceFormat.XLSM,
        ConvertSourceFormat.ODS,
    }
)


class MarkdownConverter:
    """
    源文件 -> Markdown 转换器。

    - 对外只暴露 convert(path=...)。
    - 内部按文件类型分流。
    - 所有分支最终都返回 Markdown 文本。
    """

    def __init__(
        self,
        *,
        markitdown: MarkItDown,
        parse_service: DocumentParseService,
        markdown_renderer: MarkdownRenderer,
    ) -> None:
        """初始化对象依赖。"""
        self.markitdown = markitdown
        self.parse_service = parse_service
        self.markdown_renderer = markdown_renderer

    async def convert(self, *, path: Path) -> str:
        """转换当前流程。"""
        source_format = _SUFFIX_TO_SOURCE_FORMAT.get(path.suffix.lower())
        if source_format is None:
            raise UnsupportedDocumentRouteError()

        if source_format == ConvertSourceFormat.MARKDOWN:
            return await self._convert_markdown_to_md(path=path)

        if source_format == ConvertSourceFormat.PLAIN_TEXT:
            return await self._convert_txt_to_md(path=path)

        if source_format == ConvertSourceFormat.HTML:
            return await self._convert_html_to_md(path=path)

        if source_format in _PARSE_SOURCE_FORMATS:
            return await self._convert_need_parse_to_md(path=path)

        raise UnsupportedDocumentRouteError()

    async def _convert_markdown_to_md(self, *, path: Path) -> str:
        """转换当前流程。"""
        markdown = await asyncio.to_thread(self._read_text_file, path)
        if not markdown.strip():
            raise EmptyParsedMarkdownError()

        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
        return normalized if normalized.endswith("\n") else normalized + "\n"

    async def _convert_txt_to_md(self, *, path: Path) -> str:
        """转换当前流程。"""
        text = await asyncio.to_thread(self._read_text_file, path)
        if not text.strip():
            raise EmptyParsedMarkdownError()

        markdown = self.markdown_renderer.render_to_markdown(
            content=text,
            source_format=ExportSourceFormat.PLAIN_TEXT,
        )
        if not markdown.strip():
            raise EmptyParsedMarkdownError()

        return markdown if markdown.endswith("\n") else markdown + "\n"

    async def _convert_html_to_md(self, *, path: Path) -> str:
        """转换当前流程。"""
        try:
            result = await asyncio.to_thread(
                self.markitdown.convert_local,
                str(path),
            )
        except Exception as e:
            raise DocumentConvertError("HTML to Markdown conversion failed.") from e

        markdown = result.text_content
        if not markdown.strip():
            raise EmptyParsedMarkdownError()

        normalized = markdown.strip().replace("\r\n", "\n").replace("\r", "\n")
        return normalized if normalized.endswith("\n") else normalized + "\n"

    async def _convert_need_parse_to_md(self, *, path: Path) -> str:
        """解析当前流程。"""
        try:
            result = await self.parse_service.parse_path(path)
        except DocumentParseError as e:
            raise DocumentParseFailedError(str(e)) from e

        markdown = result.text
        if not markdown.strip():
            raise EmptyParsedMarkdownError()

        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
        return normalized if normalized.endswith("\n") else normalized + "\n"

    def _read_text_file(self, path: Path) -> str:
        """读取当前流程。"""
        raw = path.read_bytes()
        if len(raw) > _TEXT_READ_BYTES_LIMIT:
            raise DocumentDecodeError()

        for encoding in _TEXT_ENCODINGS:
            try:
                return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError:
                continue

        raise DocumentDecodeError()

