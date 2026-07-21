from __future__ import annotations

from chat.application.utils.file_type_detect import FileType, detect_file_type

from .converters.base import DocumentConverter
from .converters.fallback_converter import FallbackConverter
from .converters.html_converter import HtmlConverter
from .converters.json_converter import JsonConverter
from .converters.office_converter import OfficeConverter
from .converters.plaintext_converter import PlaintextConverter
from .converters.spreadsheet_converter import SpreadsheetConverter
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
_HTML_TYPES = frozenset({"html", "htm"})
_HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_JSON_TYPES = frozenset({"json", "jsonl", "ndjson"})
_JSON_MIME_TYPES = frozenset({"application/json", "application/x-ndjson"})
_PLAINTEXT_EXTENSIONS = frozenset(
    {
        "txt",
        "text",
        "md",
        "markdown",
        "rst",
        "log",
        "py",
        "java",
        "kt",
        "kts",
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
        "vue",
        "svelte",
        "sh",
        "bash",
        "zsh",
        "ps1",
        "sql",
        "xml",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "properties",
        "env",
    }
)
_BLOCKED_EXTENSIONS = frozenset(
    {
        "7z",
        "a",
        "apk",
        "avi",
        "bz2",
        "db",
        "dll",
        "dmg",
        "exe",
        "flac",
        "gz",
        "ico",
        "iso",
        "jar",
        "jpeg",
        "jpg",
        "m4a",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "otf",
        "png",
        "rar",
        "so",
        "sqlite",
        "tar",
        "ttf",
        "wav",
        "webm",
        "webp",
        "woff",
        "woff2",
        "zip",
    }
)
_BLOCKED_LABELS = frozenset(
    {
        "7zip",
        "apk",
        "archive",
        "avi",
        "bmp",
        "database",
        "dll",
        "dmg",
        "elf",
        "exe",
        "flac",
        "font",
        "gif",
        "gzip",
        "iso",
        "jpeg",
        "macho",
        "mp3",
        "mp4",
        "ogg",
        "pebin",
        "png",
        "rar",
        "sqlite",
        "tar",
        "tiff",
        "wav",
        "webm",
        "webp",
        "zip",
    }
)
_BLOCKED_MIME_PREFIXES = ("audio/", "font/", "image/", "video/")
_BLOCKED_MIME_TYPES = frozenset(
    {
        "application/java-archive",
        "application/vnd.android.package-archive",
        "application/x-7z-compressed",
        "application/x-dosexec",
        "application/x-executable",
        "application/x-rar-compressed",
        "application/x-sharedlib",
        "application/x-sqlite3",
        "application/zip",
    }
)


class DocumentParser:
    """依据文件类型选择转换器并生成 Markdown。"""

    __slots__ = (
        "_fallback_converter",
        "_html_converter",
        "_json_converter",
        "_office_converter",
        "_pdf_converter",
        "_plaintext_converter",
        "_spreadsheet_converter",
    )

    def __init__(
        self,
        *,
        pdf_converter: DocumentConverter,
        office_converter: DocumentConverter | None = None,
        spreadsheet_converter: DocumentConverter | None = None,
        html_converter: DocumentConverter | None = None,
        json_converter: DocumentConverter | None = None,
        plaintext_converter: DocumentConverter | None = None,
        fallback_converter: DocumentConverter | None = None,
    ) -> None:
        self._pdf_converter = pdf_converter
        self._office_converter = office_converter or OfficeConverter()
        self._spreadsheet_converter = (
            spreadsheet_converter or SpreadsheetConverter()
        )
        self._html_converter = html_converter or HtmlConverter()
        self._json_converter = json_converter or JsonConverter()
        self._plaintext_converter = plaintext_converter or PlaintextConverter()
        self._fallback_converter = fallback_converter or FallbackConverter()

    async def parse(self, request: DocumentParseRequest) -> str:
        if not request.file_path.is_file():
            raise FileNotFoundError(request.file_path)

        file_name = request.display_name
        detected = detect_file_type(
            request.file_path,
            fallback_name=file_name,
        )
        detected_mime_type = (detected.mime_type or "").partition(";")[0].strip().lower()
        mime_types = frozenset({detected_mime_type}) if detected_mime_type else frozenset()

        converter = self._select_converter(detected, mime_types=mime_types)
        if converter is None and (
            detected.extension in _BLOCKED_EXTENSIONS
            or detected.label in _BLOCKED_LABELS
            or not mime_types.isdisjoint(_BLOCKED_MIME_TYPES)
            or any(
                mime_type.startswith(_BLOCKED_MIME_PREFIXES)
                for mime_type in mime_types
            )
        ):
            raise UnsupportedDocumentFormatError(
                file_name=file_name,
                extension=detected.extension,
                mime_type=detected_mime_type or None,
            )

        if converter is None and (
            detected.extension in _PLAINTEXT_EXTENSIONS
            or any(mime_type.startswith("text/") for mime_type in mime_types)
        ):
            converter = self._plaintext_converter

        return await (converter or self._fallback_converter).convert(
            request.file_path,
            file_name=file_name,
            mime_type=detected_mime_type or None,
        )

    def _select_converter(
        self,
        detected: FileType,
        *,
        mime_types: frozenset[str],
    ) -> DocumentConverter | None:
        extension = detected.extension
        label = detected.label

        if (
            extension == "pdf"
            or label == "pdf"
            or "application/pdf" in mime_types
        ):
            return self._pdf_converter

        if (
            extension in {"docx", "pptx"}
            or label in {"docx", "pptx"}
            or any(
                "wordprocessingml" in mime_type
                or "presentationml" in mime_type
                for mime_type in mime_types
            )
        ):
            return self._office_converter

        if (
            extension in _SPREADSHEET_TYPES
            or label in _SPREADSHEET_TYPES
            or not mime_types.isdisjoint(_SPREADSHEET_MIME_TYPES)
        ):
            return self._spreadsheet_converter

        if (
            extension in _HTML_TYPES
            or label in _HTML_TYPES
            or not mime_types.isdisjoint(_HTML_MIME_TYPES)
        ):
            return self._html_converter

        if (
            extension in _JSON_TYPES
            or label in _JSON_TYPES
            or not mime_types.isdisjoint(_JSON_MIME_TYPES)
        ):
            return self._json_converter

        return None
