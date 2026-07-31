import json
import zipfile
from pathlib import Path

from mineru.utils.enum_class import MakeMode

from chat.application.utils.document_parse.converters.pdf import result_archive
from chat.application.utils.document_parse.converters.pdf import converter as pdf_converter
from chat.application.utils.document_parse.converters.pdf.converter import (
    _PDF_PARSE_FORM,
)
from chat.application.utils.document_parse.converters.pdf.result_archive import (
    extract_pdf_markdown,
    _render_markdown_pages,
)


def test_pdf_request_only_returns_middle_json() -> None:
    assert _PDF_PARSE_FORM["return_md"] == "false"
    assert _PDF_PARSE_FORM["return_content_list"] == "false"
    assert _PDF_PARSE_FORM["return_middle_json"] == "true"


def test_get_pdf_converter_is_cached_by_api_url(monkeypatch) -> None:
    created: list[object] = []

    class FakePdfConverter:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    pdf_converter.get_pdf_converter.cache_clear()
    monkeypatch.setattr(pdf_converter, "PdfConverter", FakePdfConverter)

    assert pdf_converter.get_pdf_converter("https://mineru.example") is (
        pdf_converter.get_pdf_converter("https://mineru.example")
    )
    assert len(created) == 1
    assert pdf_converter.get_pdf_converter("https://other.example") is not (
        pdf_converter.get_pdf_converter("https://mineru.example")
    )

    pdf_converter.get_pdf_converter.cache_clear()


def test__render_markdown_pages_calls_mineru_once_per_page(
    monkeypatch,
) -> None:
    calls: list[tuple[list[object], str, str]] = []

    def fake_union_make(pages: list[object], mode: str, image_dir: str) -> str:
        calls.append((pages, mode, image_dir))
        return str(pages[0].get("markdown", ""))  # type: ignore[union-attr]

    monkeypatch.setattr(result_archive, "union_make", fake_union_make)
    first_page = {"markdown": "First page"}
    empty_page = {"page_idx": 4, "markdown": ""}

    markdown = _render_markdown_pages(
        {"pdf_info": [first_page, empty_page]},
    )

    assert markdown == ("<!-- page 1 -->\n\nFirst page\n\n<!-- page 5 -->")
    assert calls == [
        ([first_page], MakeMode.MM_MD, "images"),
        ([empty_page], MakeMode.MM_MD, "images"),
    ]


def test_extract_pdf_markdown_reads_archive_and_adds_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[object] = []

    def fake_render(middle_json: object) -> str:
        captured.append(middle_json)
        return "<!-- page 1 -->\n\nRendered from middle JSON"

    monkeypatch.setattr(result_archive, "_render_markdown_pages", fake_render)
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("output/full.md", "Stale server Markdown")
        archive.writestr(
            "output/document_middle.json",
            json.dumps({"pdf_info": [{"page_idx": 0}]}),
        )

    markdown = extract_pdf_markdown(
        zip_path,
        file_name="sample.pdf",
        max_output_bytes=1024,
    )

    assert markdown == "<!-- page 1 -->\n\nRendered from middle JSON"
    assert captured == [{"pdf_info": [{"page_idx": 0}]}]
