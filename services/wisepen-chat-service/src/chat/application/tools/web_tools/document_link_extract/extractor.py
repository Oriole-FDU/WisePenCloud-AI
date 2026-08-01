from __future__ import annotations

import asyncio
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any

from chat.application.tools.utils.url import validate_public_http_url_async
from chat.application.tools.web_tools.common import (
    WebContentCache,
    WebContentCacheRepository,
)
from chat.application.utils.document_parse.parse_docx import parse_docx
from chat.application.utils.document_parse.parse_pdf import (
    fast_parse_pdf,
    parse_pdf,
)
from chat.application.utils.document_parse.parse_pptx import parse_pptx
from chat.application.utils.document_parse.parse_xlsx import parse_xlsx
from chat.application.utils.file_type_detect import detect_file_type_from_bytes


_MAX_DOCUMENT_BYTES = 104_857_600

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

    __slots__ = ("_cache", "_max_document_bytes", "_session")

    def __init__(
        self,
        *,
        session: Any,
        content_cache_repository: WebContentCacheRepository | None = None,
        max_document_bytes: int = _MAX_DOCUMENT_BYTES,
    ) -> None:
        self._session = session
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

        content, headers = await self._download(url)
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

    async def _download(self, url: str) -> tuple[bytes, dict[str, str]]:
        try:
            response = await self._session.get(
                url,
                follow_redirects=False,
                timeout=300.0,
            )
        except Exception as exc:
            raise DocumentLinkExtractError(
                f"Document download failed: {exc}"
            ) from exc

        status = int(response.status)
        if (
            300 <= status < 400
            or response.history
            or response.url.strip() != url
        ):
            raise DocumentLinkExtractError("Document URL redirects are not allowed.")
        if status >= 400:
            raise DocumentLinkExtractError(
                f"Document download failed with HTTP {status}."
            )

        headers = {
            str(name).lower(): str(value)
            for name, value in response.headers.items()
        }
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

        content = bytes(response.body)
        if not content:
            raise DocumentLinkExtractError("Document response body is empty.")
        if len(content) > self._max_document_bytes:
            raise DocumentLinkExtractError(
                f"Document exceeds {self._max_document_bytes} bytes."
            )
        return content, headers

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
