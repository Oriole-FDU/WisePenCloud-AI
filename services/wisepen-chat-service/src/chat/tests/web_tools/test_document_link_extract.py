from __future__ import annotations

from pathlib import Path

import httpx
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


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], consumed_chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._consumed_chunks = consumed_chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            self._consumed_chunks.append(chunk)
            yield chunk


class _HttpxProbe:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.consumed_chunks: list[bytes] = []
        self.calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(
            200,
            headers={"cache-control": "max-age=60"},
            stream=_ChunkStream(self._chunks, self.consumed_chunks),
            request=request,
        )


def _patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    probe: _HttpxProbe,
) -> None:
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(probe.handle)

    def build_client(*args, **kwargs) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.httpx.AsyncClient",
        build_client,
    )


@pytest.mark.asyncio
async def test_document_link_extract_uses_detected_type_and_shared_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/workbook"
    sniff_bytes = b"x" * 64_000
    tail_bytes = b"lsx tail"
    probe = _HttpxProbe([sniff_bytes, tail_bytes])
    repository = _MemoryCacheRepository()
    parsed_paths: list[Path] = []
    detected_lengths: list[int] = []

    async def validate_url(value: str) -> str:
        return value

    def parse_workbook(file_path: Path, *, image_path: None) -> str:
        assert image_path is None
        assert file_path.suffix == ".xlsx"
        assert file_path.read_bytes() == sniff_bytes + tail_bytes
        parsed_paths.append(file_path)
        return "# Workbook"

    def detect_workbook(content: bytes) -> FileType:
        detected_lengths.append(len(content))
        return FileType(
            label="xlsx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            extension="",
        )

    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.validate_public_http_url_async",
        validate_url,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.detect_file_type_from_bytes",
        detect_workbook,
    )
    monkeypatch.setattr(
        "chat.application.tools.web_tools.document_link_extract.extractor.parse_xlsx",
        parse_workbook,
    )
    _patch_httpx_client(monkeypatch, probe)

    extractor = DocumentLinkExtractor(
        content_cache_repository=repository,
    )
    first = await extractor.extract(url)
    second = await extractor.extract(url)

    assert first == second == "# Workbook"
    assert probe.calls == 1
    assert probe.consumed_chunks == [sniff_bytes, tail_bytes]
    assert detected_lengths == [16_384]
    assert len(parsed_paths) == 1
    assert not parsed_paths[0].exists()
    assert (url, "document_link_extract:exact") in repository.values


@pytest.mark.asyncio
async def test_document_link_extract_separates_exact_and_fast_pdf_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/paper.pdf"
    probe = _HttpxProbe([b"%PDF-fake"])
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
    _patch_httpx_client(monkeypatch, probe)

    extractor = DocumentLinkExtractor(
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
    assert probe.calls == 2
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
    first_chunk = b"p" * 64_000
    second_chunk = b"ng tail"
    probe = _HttpxProbe([first_chunk, second_chunk])

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
    _patch_httpx_client(monkeypatch, probe)

    extractor = DocumentLinkExtractor()
    with pytest.raises(UnsupportedDocumentTypeError, match="detected png"):
        await extractor.extract(
            "https://example.test/fake.pdf",
        )
    assert probe.consumed_chunks == [first_chunk]


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
