from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chat.application.tools.web_tools import DocumentLinkExtractTool, WebFetchTool
from chat.application.tools.web_tools.common import WebContentCacheValue
from chat.application.tools.web_tools.document_link_extract import (
    DocumentLinkExtractor,
    PdfParseMethod,
    UnsupportedDocumentTypeError,
)
from chat.application.utils.file_type_detect import FileType


class _MemoryCacheRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], WebContentCacheValue] = {}

    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        return self.values.get((url, cache_variant))

    async def set_value(self, value: WebContentCacheValue) -> None:
        self.values[(value.canonical_url, value.cache_variant)] = value


class _Response:
    status = 200
    history: tuple[object, ...] = ()

    def __init__(self, url: str, body: bytes) -> None:
        self.url = url
        self.body = body
        self.headers = {"cache-control": "max-age=60"}


class _Session:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    async def get(self, url: str, **_: Any) -> _Response:
        self.calls += 1
        return _Response(url, self.body)


@pytest.mark.asyncio
async def test_document_link_extract_uses_detected_type_and_shared_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/workbook"
    session = _Session(b"xlsx bytes")
    repository = _MemoryCacheRepository()
    parsed_paths: list[Path] = []

    async def validate_url(value: str) -> str:
        return value

    def parse_workbook(file_path: Path, *, image_path: None) -> str:
        assert image_path is None
        assert file_path.suffix == ".xlsx"
        assert file_path.read_bytes() == b"xlsx bytes"
        parsed_paths.append(file_path)
        return "# Workbook"

    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.validate_public_http_url_async",
        validate_url,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.detect_file_type_from_bytes",
        lambda _: FileType(
            label="xlsx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            extension="",
        ),
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.parse_xlsx",
        parse_workbook,
    )

    extractor = DocumentLinkExtractor(
        session=session,
        content_cache_repository=repository,
    )
    first = await extractor.extract(url)
    second = await extractor.extract(url)

    assert first == second == "# Workbook"
    assert session.calls == 1
    assert len(parsed_paths) == 1
    assert not parsed_paths[0].exists()
    assert (url, "document_link_extract:exact") in repository.values


@pytest.mark.asyncio
async def test_document_link_extract_separates_exact_and_fast_pdf_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/paper.pdf"
    session = _Session(b"%PDF-fake")
    repository = _MemoryCacheRepository()

    async def validate_url(value: str) -> str:
        return value

    async def parse_exact(_: Path) -> str:
        return "exact"

    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.validate_public_http_url_async",
        validate_url,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.detect_file_type_from_bytes",
        lambda _: FileType(label="pdf", mime_type="application/pdf", extension=""),
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.parse_pdf",
        parse_exact,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.fast_parse_pdf",
        lambda _: "fast",
    )

    extractor = DocumentLinkExtractor(
        session=session,
        content_cache_repository=repository,
    )
    exact = await extractor.extract(
        url,
        pdf_method=PdfParseMethod.EXACT,
    )
    fast = await extractor.extract(
        url,
        pdf_method=PdfParseMethod.FAST,
    )

    assert (exact, fast) == ("exact", "fast")
    assert session.calls == 2
    assert {
        value.cache_variant
        for value in repository.values.values()
    } == {
        "document_link_extract:exact",
        "document_link_extract:fast",
    }


@pytest.mark.asyncio
async def test_document_link_extract_rejects_non_whitelisted_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate_url(value: str) -> str:
        return value

    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.validate_public_http_url_async",
        validate_url,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.detect_file_type_from_bytes",
        lambda _: FileType(label="png", mime_type="image/png", extension="pdf"),
    )

    extractor = DocumentLinkExtractor(session=_Session(b"png bytes"))
    with pytest.raises(UnsupportedDocumentTypeError, match="detected png"):
        await extractor.extract(
            "https://example.test/fake.pdf",
        )


@pytest.mark.asyncio
async def test_document_link_extract_tool_returns_cacheable_markdown() -> None:
    class _Extractor:
        async def extract(
            self,
            url: str,
            *,
            pdf_method: PdfParseMethod,
        ) -> str:
            assert (url, pdf_method) == (
                "https://example.test/paper.pdf",
                PdfParseMethod.FAST,
            )
            return "# Paper"

    tool = DocumentLinkExtractTool(extractor=_Extractor())
    result = await tool.execute(
        {},
        url="https://example.test/paper.pdf",
        pdf_method="fast",
    )

    assert result.visible_result == {
        "source_url": "https://example.test/paper.pdf"
    }
    assert result.cacheable_texts[0].text == "# Paper"
    assert result.cacheable_texts[0].is_md is True
    assert result.cacheable_texts[0].metadata == {
        "source_url": "https://example.test/paper.pdf"
    }
    assert "exact PDF" in tool.definition.llm_spec.description
    web_fetch = WebFetchTool(fetch_coordinator=object())
    assert "fast native text-layer extraction" in (
        web_fetch.definition.llm_spec.description
    )
