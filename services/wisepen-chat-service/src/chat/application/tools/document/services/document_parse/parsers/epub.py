from pathlib import Path
from typing import Any, Tuple

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
from chat.application.tools.document.services.document_parse.parsers.base import (
    BaseDocumentParser,
)
from chat.application.tools.document.services.document_parse.utils.text import (
    normalize_text,
)


class EpubParser(BaseDocumentParser):
    """EPUB 文档解析器，使用 MarkItDown 将 EPUB 转换为 Markdown 文本。"""

    @property
    def name(self) -> ParserName:
        """返回解析器名称 `MarkItDown`。"""
        return ParserName.MARKITDOWN

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        """支持 .epub 格式。"""
        return (".epub",)

    def __init__(self, *, converter: Any):
        """初始化 EpubParser，注入 MarkItDown 转换器实例。"""
        self.converter = converter

    async def parse(self, path: Path) -> DocumentParseResult:
        """使用 MarkItDown 转换 EPUB，返回归一化后的 DocumentParseResult。"""
        result = self.converter.convert(str(path))
        text = normalize_text(result.text_content)

        if not text:
            raise EmptyParsedContentError(str(path))

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=PageType.DOCUMENT,
            tables=[],
            metadata={"parsers": self.name},
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=DocumentType.EPUB,
            pages=[page],
            tables=[],
            metadata={
                "parsers": self.name,
                "selected_parser": self.name,
            },
            warnings=[],
        )
