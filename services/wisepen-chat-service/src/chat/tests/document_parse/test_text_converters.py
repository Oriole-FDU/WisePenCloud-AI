import codecs
from pathlib import Path

import pytest

from chat.application.utils.document_parse.errors import (
    DocumentDecodeError,
)
from chat.application.utils.document_parse.models import DocumentParseRequest
from chat.application.utils.document_parse.parser import DocumentParser


class _UnusedConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        raise AssertionError("text documents must not use a converter")


@pytest.mark.parametrize(
    ("file_name", "raw", "expected"),
    (
        ("sample.txt", b"plain text", "plain text"),
        ("sample.txt", codecs.BOM_UTF8 + "中文".encode(), "中文"),
        ("sample.md", b"# Title\n", "# Title\n"),
        ("sample.json", '{"key":"值"}'.encode(), '{"key":"值"}'),
        ("sample.jsonl", b'{"a":1}\n\n[2,3]\n', '{"a":1}\n\n[2,3]\n'),
        ("truncated.json", b'{"items":[', '{"items":['),
    ),
)
@pytest.mark.asyncio
async def test_text_documents_preserve_content(
    tmp_path: Path,
    file_name: str,
    raw: bytes,
    expected: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(raw)

    result = await DocumentParser(
        pdf_converter=_UnusedConverter(),
    ).parse(
        DocumentParseRequest(file_path=file_path),
    )

    assert result == expected


@pytest.mark.asyncio
async def test_text_documents_reject_binary_content(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "broken.txt"
    file_path.write_bytes(b"text\x00binary\x01")

    with pytest.raises(DocumentDecodeError):
        await DocumentParser(
            pdf_converter=_UnusedConverter(),
        ).parse(
            DocumentParseRequest(file_path=file_path),
        )
