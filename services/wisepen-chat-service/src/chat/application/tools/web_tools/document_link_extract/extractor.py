from __future__ import annotations

import asyncio
import tempfile
from enum import StrEnum
from pathlib import Path

import httpx

from chat.application.utils.url_security import validate_public_http_url_async
from chat.application.tools.web_tools.common import (
    WebContentCache,
)
from chat.domain.repositories import WebContentCacheRepository
from chat.application.utils.document_parse.parse_docx import parse_docx
from chat.application.utils.document_parse.parse_pdf import (
    fast_parse_pdf,
    parse_pdf,
)
from chat.application.utils.document_parse.parse_pptx import parse_pptx
from chat.application.utils.document_parse.parse_xlsx import parse_xlsx
from chat.application.utils.file_type_detect import detect_file_type_from_bytes


_MAX_DOCUMENT_BYTES = 104_857_600
_TYPE_SNIFF_BYTES = 16_384
_DOWNLOAD_CHUNK_BYTES = 64_000
_DOWNLOAD_TIMEOUT_SECONDS = 300.0


class PdfParseMethod(StrEnum):
    EXACT = "exact"
    FAST = "fast"


class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"


_DOCUMENT_TYPE_BY_LABEL = {item.value: item for item in DocumentType}
_DOCUMENT_TYPE_BY_MIME = {
    "application/pdf": DocumentType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentType.DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentType.XLSX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": DocumentType.PPTX,
}


class DocumentLinkExtractError(RuntimeError):
    pass


class UnsupportedDocumentTypeError(DocumentLinkExtractError):
    pass


class DocumentLinkExtractor:
    """下载公开文档直链，校验真实文件类型后解析为 Markdown。"""

    __slots__ = ("_cache", "_max_document_bytes")

    def __init__(
        self,
        *,
        content_cache_repository: WebContentCacheRepository | None = None,
        max_document_bytes: int = _MAX_DOCUMENT_BYTES,
    ) -> None:
        self._cache = WebContentCache(repository=content_cache_repository)
        self._max_document_bytes = max(1, int(max_document_bytes))

    async def extract(
        self,
        url: str,
        *,
        pdf_method: PdfParseMethod = PdfParseMethod.EXACT,
    ) -> str:
        url = await validate_public_http_url_async(url.strip())
        cache_variant = f"document_link_extract:{pdf_method.value}"
        cached = await self._cache.read(
            url=url,
            cache_variant=cache_variant,
        )
        if cached is not None:
            return cached.text

        content, headers, document_type = await self._download(url)

        with tempfile.TemporaryDirectory(prefix="document_link_extract_") as temp_dir:
            file_path = Path(temp_dir) / f"document.{document_type.value}"
            await asyncio.to_thread(file_path.write_bytes, content)
            markdown = await self._parse(
                file_path,
                document_type=document_type,
                pdf_method=pdf_method,
            )

        markdown = markdown.strip()
        if not markdown:
            raise DocumentLinkExtractError("Document parser returned no Markdown content.")

        await self._cache.write(
            url=url,
            headers=headers,
            text=markdown,
            is_md=True,
            cache_variant=cache_variant,
        )
        return markdown

    async def _download(self, url: str) -> tuple[bytes, dict[str, str], DocumentType]:
        headers: dict[str, str] = {}
        content = bytearray()
        document_type: DocumentType | None = None

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            ) as client:
                async with client.stream("GET", url) as response:
                    self._check_response(response)
                    headers = {
                        name.lower(): value
                        for name, value in response.headers.items()
                    }
                    self._check_declared_size(headers)

                    async for chunk in response.aiter_bytes(
                        chunk_size=_DOWNLOAD_CHUNK_BYTES,
                    ):
                        if not chunk:
                            continue
                        content.extend(chunk)
                        if len(content) > self._max_document_bytes:
                            raise DocumentLinkExtractError(
                                f"Document exceeds {self._max_document_bytes} bytes."
                            )
                        if (
                            document_type is None
                            and len(content) >= _TYPE_SNIFF_BYTES
                        ):
                            # 先用有限窗口判定直链真实类型；不支持则不继续消费正文。
                            document_type = await self._detect_supported_type(
                                bytes(content[:_TYPE_SNIFF_BYTES])
                            )

                    if not content:
                        raise DocumentLinkExtractError("Document response body is empty.")
                    if document_type is None:
                        document_type = await self._detect_supported_type(bytes(content))
        except httpx.HTTPError as exc:
            raise DocumentLinkExtractError(
                f"Document download failed: {exc}"
            ) from exc

        return bytes(content), headers, document_type

    def _check_response(self,response: httpx.Response) -> None:
        status = response.status_code
        if 300 <= status < 400:
            raise DocumentLinkExtractError("Document URL redirects are not allowed.")
        if status >= 400:
            raise DocumentLinkExtractError(
                f"Document download failed with HTTP {status}."
            )

    def _check_declared_size(self, headers: dict[str, str]) -> None:
        content_length = headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise DocumentLinkExtractError(
                    "Document response has an invalid content length."
                ) from exc
            if declared_size < 0:
                raise DocumentLinkExtractError(
                    "Document response has a negative content length."
                )
            if declared_size > self._max_document_bytes:
                raise DocumentLinkExtractError(
                    f"Document exceeds {self._max_document_bytes} bytes."
                )

    async def _detect_supported_type(self, content: bytes) -> DocumentType:
        detected = await asyncio.to_thread(detect_file_type_from_bytes, content)
        document_type = (
            _DOCUMENT_TYPE_BY_LABEL.get(detected.label)
            or _DOCUMENT_TYPE_BY_MIME.get(detected.mime_type)
        )
        if document_type is None:
            raise UnsupportedDocumentTypeError(
                "Only PDF, DOCX, XLSX, and PPTX document links are supported; "
                f"detected {detected.label or detected.mime_type or 'unknown'}."
            )
        return document_type

    async def _parse(
        self,
        file_path: Path,
        *,
        document_type: DocumentType,
        pdf_method: PdfParseMethod,
    ) -> str:
        if document_type is DocumentType.PDF:
            if pdf_method is PdfParseMethod.EXACT:
                return await parse_pdf(file_path)
            return await asyncio.to_thread(fast_parse_pdf, file_path)
        if document_type is DocumentType.DOCX:
            return await asyncio.to_thread(parse_docx, file_path)
        if document_type is DocumentType.XLSX:
            return await asyncio.to_thread(parse_xlsx, file_path, image_path=None)
        return await asyncio.to_thread(parse_pptx, file_path, image_path=None)
