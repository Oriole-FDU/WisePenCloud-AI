from pathlib import Path

import pytest

from chat.application.utils.document_parse.errors import (
    UnsupportedDocumentFormatError,
)
from chat.application.utils.document_parse import parser as parser_module
from chat.application.utils.document_parse.parser import DocumentParser
from chat.application.utils.file_type_detect import FileType


class _Converter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        return self.name


@pytest.mark.parametrize(
    ("file_name", "extension", "label", "mime_type", "expected"),
    (
        ("sample.pdf", "pdf", "pdf", "application/pdf", "pdf"),
        ("sample.docx", "docx", "zip", "application/zip", "office"),
        (
            "sample.pptx",
            "pptx",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "office",
        ),
        ("sample.csv", "csv", "csv", "text/csv", "spreadsheet"),
        (
            "sample.xlsx",
            "xlsx",
            "zip",
            "application/zip",
            "spreadsheet",
        ),
        ("sample.html", "html", "html", "text/html", "generic"),
        ("sample.epub", "epub", "epub", "application/epub+zip", "generic"),
        ("notebook.ipynb", "ipynb", "ipynb", "application/json", "generic"),
        ("sample.json", "json", "json", "application/json", "content"),
        ("sample.jsonl", "jsonl", "jsonl", "application/x-ndjson", "content"),
        ("sample.py", "py", "txt", "text/plain", "content"),
    ),
)
@pytest.mark.asyncio
async def test_router_uses_specific_format_before_generic_rules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file_name: str,
    extension: str,
    label: str,
    mime_type: str,
    expected: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(b"content")
    monkeypatch.setattr(
        parser_module,
        "detect_file_type",
        lambda *_args, **_kwargs: FileType(
            label=label,
            mime_type=mime_type,
            extension=extension,
        ),
    )
    parser = DocumentParser(
        pdf_converter=_Converter("pdf"),
        office_converter=_Converter("office"),
        spreadsheet_converter=_Converter("spreadsheet"),
        generic_converter=_Converter("generic"),
    )

    result = await parser.parse(file_path)

    assert result == expected


@pytest.mark.parametrize(
    ("file_name", "extension", "label", "mime_type"),
    (
        ("sample.unknown", "unknown", "unknown", "application/octet-stream"),
        ("sample.zip", "zip", "zip", "application/zip"),
        ("sample.png", "png", "png", "image/png"),
        ("sample.msg", "msg", "msg", "application/vnd.ms-outlook"),
        ("calendar.ics", "ics", "calendar", "text/calendar"),
    ),
)
@pytest.mark.asyncio
async def test_parser_rejects_unsupported_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file_name: str,
    extension: str,
    label: str,
    mime_type: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(b"content")
    monkeypatch.setattr(
        parser_module,
        "detect_file_type",
        lambda *_args, **_kwargs: FileType(
            label=label,
            mime_type=mime_type,
            extension=extension,
        ),
    )
    parser = DocumentParser(
        pdf_converter=_Converter("pdf"),
        generic_converter=_Converter("generic"),
    )

    with pytest.raises(UnsupportedDocumentFormatError):
        await parser.parse(file_path)


@pytest.mark.asyncio
async def test_detected_binary_type_cannot_bypass_binary_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.data"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        parser_module,
        "detect_file_type",
        lambda *_args, **_kwargs: FileType(
            label="png",
            mime_type="image/png",
            extension="data",
        ),
    )
    parser = DocumentParser(
        pdf_converter=_Converter("pdf"),
        generic_converter=_Converter("generic"),
    )

    with pytest.raises(UnsupportedDocumentFormatError):
        await parser.parse(file_path)
