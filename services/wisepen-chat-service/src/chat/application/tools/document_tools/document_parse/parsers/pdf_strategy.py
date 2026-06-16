from __future__ import annotations

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

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        try:
            pdf_path = Path(request.file_path)
            page_markdowns: list[str] = []

            with fitz.open(str(pdf_path)) as document:
                for page_index in range(document.page_count):
                    page_number = page_index + 1
                    page = document.load_page(page_index)
                    # 文本页走 PyMuPDF4LLM，扫描页才尝试 OCR，避免不必要的外部调用。
                    if classify_page(
                        page,
                        scan_coverage=self._scan_coverage,
                        min_text_chars=self._min_text_chars,
                    ) == "text":
                        rendered = pymupdf4llm.to_markdown(str(pdf_path), pages=[page_index])
                        page_markdowns.append(
                            self._with_page_marker(page_number, str(rendered or "").strip())
                        )
                    else:
                        if self._ocr_client is None:
                            continue
                        try:
                            ocr_result = await self._ocr_client.parse_page(
                                file_path=pdf_path,
                                page_number=page_number,
                            )
                        except Exception:
                            # 单页 OCR 失败不拖垮整份 PDF，保留其它页可解析内容。
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

    @staticmethod
    def _with_page_marker(page_number: int, markdown: str) -> str:
        marker = f"<!-- page {page_number} -->"
        return marker if not markdown else f"{marker}\n\n{markdown}"


def classify_page(
    page: fitz.Page,
    *,
    scan_coverage: float = 0.85,
    min_text_chars: int = 20,
) -> Literal["text", "scanned"]:
    """粗略判断 PDF 页面是文本页还是扫描页。

    Args:
        page: PyMuPDF 页面对象。
        scan_coverage: 单张图片覆盖页面面积超过该比例时，倾向认为是扫描页。
        min_text_chars: 页面可抽取文本达到该长度时，倾向认为是文本页。

    Returns:
        `"text"` 表示优先走文本抽取，`"scanned"` 表示优先走 OCR。
    """
    page_area = abs(page.rect)
    if page_area == 0:
        return "scanned"

    has_dominant_image = _max_image_coverage(page, page_area) >= scan_coverage
    has_real_text = len(page.get_text("text").strip()) >= min_text_chars

    if not has_dominant_image and has_real_text:
        return "text"

    return "scanned"


def _max_image_coverage(page: fitz.Page, page_area: float) -> float:
    # 只看最大图片块覆盖率，避免多个小图标误判为扫描件。
    max_coverage = 0.0
    for block in page.get_text("rawdict", flags=0)["blocks"]:
        if block["type"] == 1:
            coverage = abs(fitz.Rect(block["bbox"])) / page_area
            if coverage > max_coverage:
                max_coverage = coverage
    return max_coverage
