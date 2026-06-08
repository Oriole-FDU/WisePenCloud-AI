from pathlib import Path
from typing import Any

from chat.application.tools.document.services.document_parse.enums import (
    DocumentType,
    PageType,
    ParserName,
)
from chat.application.tools.document.services.document_parse.errors import EmptyParsedContentError
from chat.application.tools.document.services.document_parse.models import (
    DocumentParseResult,
    ParsedPage,
)
from chat.application.tools.document.services.document_parse.utils.text import normalize_text


class OfficeFallbackParser:
    """Office 文档兜底解析器，当 docling 主链路失败时使用 MarkItDown 解析。"""

    def __init__(self, *, converter: Any):
        """初始化 OfficeFallbackParser，注入 MarkItDown 转换器实例。"""
        self.converter = converter

    def parse(self, path: Path, *, file_type: DocumentType) -> DocumentParseResult:
        """使用 MarkItDown 解析 Office 文档，返回带归一化文本的 DocumentParseResult。"""
        result = self.converter.convert(str(path))
        text = normalize_text(result.text_content)

        if not text:
            raise EmptyParsedContentError(str(path))

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=PageType.DOCUMENT,
            tables=[],
            metadata={"parsers": ParserName.MARKITDOWN},
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=file_type,
            pages=[page],
            tables=[],
            metadata={"parsers": ParserName.MARKITDOWN},
            warnings=[],
        )
