from __future__ import annotations

import asyncio  # 补全导入
import tempfile  # 补全导入
import uuid  # 引入 UUID 防止并发临时文件冲突
from pathlib import Path
from typing import Any, Literal

import fitz
import pymupdf4llm

from chat.application.tools.document_tools.document_parse.errors import PrimaryParserError
from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseMonitorName,
    DocumentParseRequest,
    DocumentParseResult,
)
from chat.application.tools.tool_settings import tool_settings


class PdfParseStrategy:
    """PDF 解析策略，按页在文本抽取和 OCR 之间切换。"""

    def __init__(
            self,
            *,
            ocr_client: Any | None = None,
            scan_coverage: float = 0.85,
            min_text_chars: int = 20,
    ) -> None:
        self._ocr_client = ocr_client
        self._scan_coverage = scan_coverage
        self._min_text_chars = min_text_chars
        self._sanitize_timeout_seconds = tool_settings.PDF_SANITIZE_TIMEOUT_SECONDS

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        pdf_path = Path(request.file_path)
        cleaned_path: Path | None = None

        try:
            cleaned_path = await asyncio.wait_for(
                asyncio.to_thread(self._sanitize_pdf_sync, pdf_path),
                timeout=self._sanitize_timeout_seconds,
            )

            with fitz.open(str(cleaned_path)) as document:
                page_count = document.page_count
                text_page_indices = [
                    page_index
                    for page_index in range(page_count)
                    if classify_page(
                        document.load_page(page_index),
                        scan_coverage=self._scan_coverage,
                        min_text_chars=self._min_text_chars,
                    ) == "text"
                ]

            rendered_by_index: dict[int, str] = {}
            if text_page_indices:
                chunks = await asyncio.to_thread(
                    pymupdf4llm.to_markdown,
                    str(cleaned_path),  # 传路径,让 worker 线程自己开一份,不共享 C 层状态
                    pages=text_page_indices,
                    page_chunks=True,
                )
                rendered_by_index = {
                    page_index: str(chunk.get("text") or "").strip()
                    for page_index, chunk in zip(text_page_indices, chunks)
                }

            page_markdowns: list[str] = []
            for page_index in range(page_count):
                page_number = page_index + 1
                if (rendered := rendered_by_index.get(page_index)) is not None:
                    page_markdowns.append(self._with_page_marker(page_number, rendered))
                    continue
                if self._ocr_client is None:
                    continue
                try:
                    ocr_result = await self._ocr_client.parse_page(
                        file_path=cleaned_path,
                        page_number=page_number,
                    )
                except Exception:
                    continue
                page_markdowns.append(ocr_result.markdown_with_page_marker())

            return DocumentParseResult(
                markdown="\n\n".join(part for part in page_markdowns if part.strip()).strip(),
            )

        except PrimaryParserError:
            raise
        except Exception as e:
            raise PrimaryParserError(
                "PDF parser failed.",
                parser_name=DocumentParseMonitorName.PDF,
                cause=e,
            ) from e
        finally:
            if cleaned_path and cleaned_path.exists():
                try:
                    cleaned_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _with_page_marker(page_number: int, markdown: str) -> str:
        marker = f""
        return marker if not markdown else f"{marker}\n\n{markdown}"

    @staticmethod
    def _sanitize_pdf_sync(pdf_path: Path) -> Path:
        """用 fitz 自带的 garbage collection 强制重写一份干净副本，并增加随机 UUID 规避并发冲突。"""
        unique_id = uuid.uuid4().hex
        cleaned_path = Path(tempfile.gettempdir()) / f"{pdf_path.stem}_{unique_id}.cleaned.pdf"

        with fitz.open(str(pdf_path)) as document:
            document.save(
                str(cleaned_path),
                garbage=4,
                deflate=True,
                clean=True
            )
        return cleaned_path


def classify_page(
        page: fitz.Page,
        *,
        scan_coverage: float = 0.85,
        min_text_chars: int = 20,
) -> Literal["text", "scanned"]:
    page_area = abs(page.rect)
    if page_area == 0:
        return "scanned"

    has_dominant_image = _max_image_coverage(page, page_area) >= scan_coverage
    has_real_text = len(page.get_text("text").strip()) >= min_text_chars

    if not has_dominant_image and has_real_text:
        return "text"

    return "scanned"


def _max_image_coverage(page: fitz.Page, page_area: float) -> float:
    max_coverage = 0.0
    for block in page.get_text("rawdict", flags=0)["blocks"]:
        if block["type"] == 1:
            coverage = abs(fitz.Rect(block["bbox"])) / page_area
            if coverage > max_coverage:
                max_coverage = coverage
    return max_coverage