from __future__ import annotations

from functools import lru_cache

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter

from chat.application.tools.document_tools.document_parse.errors import PrimaryParserError
from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseMonitorName,
    DocumentParseRequest,
    DocumentParseResult,
)


class DoclingParser:

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        try:
            result = _get_converter().convert(str(request.file_path))
            return DocumentParseResult(
                markdown=str(result.document.export_to_markdown() or "").strip(),
            )
        except Exception as e:
            raise PrimaryParserError(
                "Docling parser failed.",
                parser_name=DocumentParseMonitorName.DOCLING,
                cause=e,
            ) from e


@lru_cache(maxsize=1)
def _get_converter() -> DocumentConverter:
    # DocumentConverter 初始化较重，模块内缓存即可，不膨胀应用容器。
    return DocumentConverter(
        allowed_formats=[
            InputFormat.DOCX,
            InputFormat.PPTX,
            InputFormat.HTML,
        ]
    )
