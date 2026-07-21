import json
import zipfile
from pathlib import Path

from chat.application.utils.document_parse.converters.pdf.page_markers import (
    insert_page_markers,
)
from chat.application.utils.document_parse.converters.pdf.result_archive import (
    extract_pdf_markdown,
)


def test_insert_page_markers_before_markdown_blocks() -> None:
    markdown = "# Title\n\nFirst page\n\nSecond page"
    content_list = [
        {"type": "text", "text": "Title", "page_idx": 0},
        {"type": "text", "text": "Second page", "page_idx": 1},
    ]

    assert insert_page_markers(markdown, content_list) == (
        "<!-- page 1 -->\n\n"
        "# Title\n\nFirst page\n\n"
        "<!-- page 2 -->\n\nSecond page"
    )


def test_insert_page_markers_returns_original_for_ambiguous_anchor() -> None:
    markdown = "Repeated\n\nRepeated"
    content_list = [
        {"type": "text", "text": "Repeated", "page_idx": 0},
    ]

    assert insert_page_markers(markdown, content_list) == markdown


def test_extract_pdf_markdown_reads_archive_and_adds_pages(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("output/full.md", "First page\n\nSecond page")
        archive.writestr(
            "output/document_content_list.json",
            json.dumps(
                [
                    {"type": "text", "text": "First page", "page_idx": 0},
                    {"type": "text", "text": "Second page", "page_idx": 1},
                ]
            ),
        )

    markdown = extract_pdf_markdown(
        zip_path,
        file_name="sample.pdf",
        max_output_bytes=1024,
    )

    assert markdown.startswith("<!-- page 1 -->")
    assert "<!-- page 2 -->" in markdown
