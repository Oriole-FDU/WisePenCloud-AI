from __future__ import annotations

from markitdown import MarkItDown

from chat.application.tools.document_tools.document_parse.errors import FallbackParserError
from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseMonitorName,
    DocumentParseRequest,
    DocumentParseResult,
)


class MarkItDownParser:
    """通用兜底解析器，用于处理未覆盖格式或主解析失败的情况。"""

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        try:
            result = MarkItDown().convert_local(str(request.file_path))
            return DocumentParseResult(
                markdown=str(result.text_content or "").strip(),
            )
        except Exception as e:
            raise FallbackParserError(
                "MarkItDown fallback failed.",
                parser_name=DocumentParseMonitorName.FALLBACK,
                cause=e,
            ) from e
