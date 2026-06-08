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


class OfficePrimaryParser:
    """Office 文档主解析器，使用 docling 将 Office 文档转换为 Markdown。"""

    def __init__(self, *, converter: Any):
        """初始化 OfficePrimaryParser，注入 docling 转换器实例。"""
        self.converter = converter

    def parse(self, path: Path, *, file_type: DocumentType) -> DocumentParseResult:
        """使用 docling 解析 Office 文档，返回带归一化文本的 DocumentParseResult。"""
        result = self.converter.convert(str(path))
        text = normalize_text(result.document.export_to_markdown())

        if not text:
            raise EmptyParsedContentError(str(path))

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=PageType.DOCUMENT,
            tables=[],
            metadata={"parsers": ParserName.DOCLING},
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=file_type,
            pages=[page],
            tables=[],
            metadata={"parsers": ParserName.DOCLING},
            warnings=[],
        )
