import pytest
import pymupdf

from chat.application.utils.pdf_fast_fetch import pdf_to_content


@pytest.fixture
def text_pdf() -> pymupdf.Document:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Fast PDF content")
    yield document
    document.close()


def test_pdf_to_plain_text(text_pdf: pymupdf.Document):
    content = pdf_to_content(text_pdf, format="plain_text")

    assert content == "Fast PDF content"


def test_pdf_to_markdown_uses_native_entry(text_pdf: pymupdf.Document):
    content = pdf_to_content(text_pdf, format="markdown")

    assert "Fast PDF content" in content


def test_pdf_to_content_rejects_unknown_format(text_pdf: pymupdf.Document):
    with pytest.raises(ValueError, match="Unsupported PDF content format"):
        pdf_to_content(text_pdf, format="html")

