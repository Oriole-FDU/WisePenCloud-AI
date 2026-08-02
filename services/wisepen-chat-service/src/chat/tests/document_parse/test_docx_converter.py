from __future__ import annotations

import zipfile
from pathlib import Path

from chat.application.utils.document_parse.parse_docx import parse_docx

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def test_parse_docx_ignores_empty_page_break_paragraph(tmp_path: Path) -> None:
    file_path = tmp_path / "empty-break.docx"
    _write_docx(
        file_path,
        """
        <w:p><w:r><w:t>Before</w:t></w:r></w:p>
        <w:p><w:r><w:br w:type="page"/></w:r></w:p>
        <w:p><w:r><w:t>After</w:t></w:r></w:p>
        """,
    )

    markdown = parse_docx(file_path)

    assert "<!-- page 2 -->" not in markdown
    assert "Before" in markdown
    assert "After" in markdown


def test_parse_docx_keeps_inline_page_break_with_visible_text(tmp_path: Path) -> None:
    file_path = tmp_path / "inline-break.docx"
    _write_docx(
        file_path,
        """
        <w:p><w:r><w:t>Before</w:t><w:br w:type="page"/><w:t>After</w:t></w:r></w:p>
        """,
    )

    markdown = parse_docx(file_path)

    assert "<!-- page 1 -->\n\nBefore" in markdown
    assert "<!-- page 2 -->\n\nAfter" in markdown


def _write_docx(file_path: Path, body: str) -> None:
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>'
    with zipfile.ZipFile(file_path, "w") as archive:
        archive.writestr("word/document.xml", document)
