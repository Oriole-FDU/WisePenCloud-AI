from pathlib import Path
from types import SimpleNamespace

import pytest
from docling_core.types.doc import ImageRefMode

from chat.application.utils.document_parse.converters import (
    generic_converter as generic_module,
    office_converter as office_module,
)
from chat.application.utils.document_parse.converters.generic_converter import (
    GenericConverter,
)
from chat.application.utils.document_parse.converters.office_converter import (
    OfficeConverter,
)


class _Document:
    def export_to_markdown(self, **kwargs: object) -> str:
        assert kwargs["image_mode"] == ImageRefMode.EMBEDDED
        assert kwargs["traverse_pictures"] is True
        return "# Title\n\n![image](data:image/png;base64,AAAA)"


class _DoclingConverter:
    def convert(self, file_path: Path) -> SimpleNamespace:
        return SimpleNamespace(document=_Document())


class _MarkItDown:
    def convert_local(self, file_path: Path) -> SimpleNamespace:
        return SimpleNamespace(text_content="# Fallback")


@pytest.mark.parametrize("file_name", ("sample.docx", "sample.pptx"))
@pytest.mark.asyncio
async def test_office_converter_handles_docx_and_pptx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file_name: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(b"office")
    monkeypatch.setattr(office_module, "get_docling_converter", _DoclingConverter)

    result = await OfficeConverter().convert(
        file_path,
        file_name=file_name,
    )

    assert "data:image/png;base64,AAAA" in result
    assert str(tmp_path) not in result


@pytest.mark.asyncio
async def test_generic_converter_uses_markitdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.epub"
    file_path.write_bytes(b"document")
    monkeypatch.setattr(generic_module, "get_markitdown", _MarkItDown)

    result = await GenericConverter().convert(
        file_path,
        file_name=file_path.name,
    )

    assert result == "# Fallback"


@pytest.mark.asyncio
async def test_generic_converter_handles_html(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.html"
    file_path.write_text(
        """
        <html>
          <body>
            <h1>Title</h1>
            <a href="https://example.com">Link</a>
            <script>window.executed = true</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    result = await GenericConverter().convert(
        file_path,
        file_name=file_path.name,
    )

    assert "# Title" in result
    assert "https://example.com" in result
    assert "window.executed" not in result
