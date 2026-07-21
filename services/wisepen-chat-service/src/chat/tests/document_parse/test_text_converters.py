import codecs
from pathlib import Path

import pytest

from chat.application.utils.document_parse.converters.json_converter import (
    JsonConverter,
)
from chat.application.utils.document_parse.converters.plaintext_converter import (
    PlaintextConverter,
)
from chat.application.utils.document_parse.errors import (
    DocumentDecodeError,
    DocumentParserError,
)


@pytest.mark.parametrize(
    ("file_name", "raw", "expected"),
    (
        ("sample.txt", b"plain text", "plain text"),
        ("sample.txt", codecs.BOM_UTF8 + "中文".encode(), "中文"),
        ("sample.md", b"# Title\n", "# Title\n"),
    ),
)
@pytest.mark.asyncio
async def test_plaintext_converter_preserves_content(
    tmp_path: Path,
    file_name: str,
    raw: bytes,
    expected: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(raw)

    result = await PlaintextConverter().convert(
        file_path,
        file_name=file_name,
    )

    assert result == expected


@pytest.mark.asyncio
async def test_plaintext_converter_rejects_binary_content(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "broken.txt"
    file_path.write_bytes(b"text\x00binary\x01")

    with pytest.raises(DocumentDecodeError):
        await PlaintextConverter().convert(
            file_path,
            file_name=file_path.name,
        )


@pytest.mark.asyncio
async def test_json_and_jsonl_are_normalized(tmp_path: Path) -> None:
    json_path = tmp_path / "sample.json"
    json_path.write_text('{"key":"值"}', encoding="utf-8")
    jsonl_path = tmp_path / "sample.jsonl"
    jsonl_path.write_text('{"a":1}\n\n[2,3]\n', encoding="utf-8")

    json_result = await JsonConverter().convert(
        json_path,
        file_name=json_path.name,
    )
    jsonl_result = await JsonConverter().convert(
        jsonl_path,
        file_name=jsonl_path.name,
    )

    assert json_result == '```json\n{\n  "key": "值"\n}\n```'
    assert jsonl_result == '{"a": 1}\n[2, 3]'


@pytest.mark.asyncio
async def test_invalid_json_reports_source_location(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.json"
    file_path.write_text('{"key":}', encoding="utf-8")

    with pytest.raises(DocumentParserError, match=r"line 1, column 8"):
        await JsonConverter().convert(
            file_path,
            file_name=file_path.name,
        )
