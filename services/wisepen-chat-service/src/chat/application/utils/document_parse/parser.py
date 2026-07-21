from __future__ import annotations

import asyncio

from chat.application.utils.file_type_detect import FileType, detect_file_type

from .converters.base import DocumentConverter
from .converters.generic_converter import GenericConverter
from .converters.office_converter import OfficeConverter
from .converters.spreadsheet_converter import SpreadsheetConverter
from .converters.utils import decode_text
from .errors import UnsupportedDocumentFormatError
from .models import DocumentParseRequest

_SPREADSHEET_TYPES = frozenset({"csv", "tsv", "xls", "xlsx"})
_SPREADSHEET_MIME_TYPES = frozenset(
    {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/tab-separated-values",
    }
)
_GENERIC_TYPES = frozenset({"html", "htm", "epub", "ipynb"})
_GENERIC_MIME_TYPES = frozenset(
    {
        "application/epub",
        "application/epub+zip",
        "application/x-epub+zip",
        "application/vnd.jupyter",
        "application/xhtml+xml",
        "text/html",
    }
)
_PLAINTEXT_MIME_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/json",
        "application/x-ndjson",
        "application/xml",
        "text/xml",
        "application/yaml",
        "text/yaml",
    }
)
_PLAINTEXT_EXTENSIONS = frozenset(
    {
        # 普通文本
        "txt",
        "md",
        "log",

        # 常见结构化文本
        "json",
        "jsonl",
        "ndjson",
        "yaml",
        "yml",
        "xml",
        "toml",
        "ini",

        # 常见源代码
        "py",
        "java",
        "c",
        "h",
        "cpp",
        "hpp",
        "cs",
        "go",
        "rs",
        "js",
        "jsx",
        "ts",
        "tsx",
        "sh",
        "sql",
    }
)


class DocumentParser:
    """依据文件类型选择转换器并生成模型可读文本。"""

    __slots__ = (
        "_generic_converter",
        "_office_converter",
        "_pdf_converter",
        "_spreadsheet_converter",
    )

    def __init__(
        self,
        *,
        pdf_converter: DocumentConverter,
        office_converter: DocumentConverter | None = None,
        spreadsheet_converter: DocumentConverter | None = None,
        generic_converter: DocumentConverter | None = None,
    ) -> None:
        self._pdf_converter = pdf_converter
        self._office_converter = office_converter or OfficeConverter()
        self._spreadsheet_converter = (
            spreadsheet_converter or SpreadsheetConverter()
        )
        self._generic_converter = generic_converter or GenericConverter()

    async def parse(self, request: DocumentParseRequest) -> str:
        if not request.file_path.is_file():
            raise FileNotFoundError(request.file_path)

        file_name = request.display_name
        detected = detect_file_type(
            request.file_path,
            fallback_name=file_name,
        )
        # 路由只信任本地检测结果，不接受上游声明的 MIME 类型。
        mime_type = (detected.mime_type or "").partition(";")[0].strip().lower()

        converter = self._select_converter(detected, mime_type=mime_type)
        if converter is not None:
            return await converter.convert(
                request.file_path,
                file_name=file_name,
                mime_type=mime_type or None,
            )

        # HTML、EPUB、Notebook 由 MarkItDown 统一处理；纯文本无需转换。
        if (
            detected.extension in _GENERIC_TYPES
            or detected.label in _GENERIC_TYPES
            or mime_type in _GENERIC_MIME_TYPES
        ):
            return await self._generic_converter.convert(
                request.file_path,
                file_name=file_name,
                mime_type=mime_type or None,
            )

        if (
            detected.extension in _PLAINTEXT_EXTENSIONS
            or detected.label in _PLAINTEXT_EXTENSIONS
            or mime_type in _PLAINTEXT_MIME_TYPES
        ):
            raw = await asyncio.to_thread(request.file_path.read_bytes)
            return decode_text(raw, file_name=file_name)

        raise UnsupportedDocumentFormatError(
            file_name=file_name,
            extension=detected.extension,
            mime_type=mime_type or None,
        )

    def _select_converter(
        self,
        detected: FileType,
        *,
        mime_type: str,
    ) -> DocumentConverter | None:
        extension = detected.extension
        label = detected.label

        if (
            extension == "pdf"
            or label == "pdf"
            or mime_type == "application/pdf"
        ):
            return self._pdf_converter

        if (
            extension in {"docx", "pptx"}
            or label in {"docx", "pptx"}
            or "wordprocessingml" in mime_type
            or "presentationml" in mime_type
        ):
            return self._office_converter

        if (
            extension in _SPREADSHEET_TYPES
            or label in _SPREADSHEET_TYPES
            or mime_type in _SPREADSHEET_MIME_TYPES
        ):
            return self._spreadsheet_converter

        return None
