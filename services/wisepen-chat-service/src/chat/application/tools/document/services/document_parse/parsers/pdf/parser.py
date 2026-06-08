import asyncio
import json
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz

from chat.application.tools.document.services.document_parse.enums import (
    DocumentType,
    PageType,
    ParserName,
)
from chat.application.tools.document.services.document_parse.errors import (
    DocumentParseError,
    UnsupportedDocumentFormatError,
)
from chat.application.tools.document.services.document_parse.models import (
    DocumentParseResult,
    ParsedPage,
    ParsedTable,
)
from chat.application.tools.document.services.document_parse.ocr.image_adapter import OcrImageAdapter
from chat.application.tools.document.services.document_parse.parsers.base import (
    BaseDocumentParser,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.docling import (
    DoclingPdfExtractor,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.page_classifier import (
    PageClassifier,
    PageProbe,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.table_extractor import (
    TableExtractor,
)
from chat.application.tools.document.services.document_parse.utils.text import (
    normalize_text,
)
from common.logger import log_fail

_BACKEND_DOCLING = ParserName.DOCLING_PDF_TABLE_NO_OCR
_BACKEND_PYMUPDF = ParserName.PYMUPDF
_BACKEND_PADDLEOCR = ParserName.PADDLEOCR
_BACKEND_NONE = "none"


@dataclass(slots=True)
class PageDraft:
    """PDF fallback 阶段的页面草稿。"""

    page_index: int
    page_type: PageType
    text: str
    probe: Optional[PageProbe] = None
    ocr_used: bool = False
    text_backend: str = _BACKEND_NONE
    image_path: Optional[Path] = None
    tables: List[ParsedTable] = field(default_factory=list)


class PdfParser(BaseDocumentParser):
    """PDF 解析器，编排 Docling 全文提取与 PyMuPDF 逐页 fallback 流程，包含 OCR 和表格抽取。"""

    @property
    def name(self) -> ParserName:
        """返回解析器名称 `PdfParser`。"""
        return ParserName.PDF

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        """支持 .pdf 格式。"""
        return (".pdf",)

    def __init__(
        self,
        *,
        classifier: PageClassifier,
        ocr_adapter: OcrImageAdapter,
        docling_extractor: DoclingPdfExtractor,
        table_extractor: TableExtractor,
        scanned_ocr_max_pages: int = 80,
        scanned_ocr_concurrency: int = 2,
        max_pages: int = 80,
        docling_min_text_chars: int = 30,
        render_dpi: int = 220,
        render_max_image_pixels: int = 36_000_000,
        render_alpha: bool = False,
    ) -> None:
        """初始化 PdfParser，注入页面分类器、OCR 适配器、Docling 提取器、表格提取器及渲染参数。"""
        self.classifier = classifier
        self.ocr_adapter = ocr_adapter
        self.docling_extractor = docling_extractor
        self.table_extractor = table_extractor
        self._scanned_ocr_max_pages = scanned_ocr_max_pages
        self._scanned_ocr_concurrency = max(1, scanned_ocr_concurrency)
        self._max_pages = max_pages
        self._docling_min_text_chars = docling_min_text_chars
        self._render_dpi = render_dpi
        self._render_max_image_pixels = render_max_image_pixels
        self._render_alpha = render_alpha

    async def parse(self, path: Path) -> DocumentParseResult:
        """Docling 整文解析优先；失败或文本过短时降级到 PyMuPDF fallback 流程。"""
        warnings: List[str] = []

        try:
            docling_result = await asyncio.to_thread(self.docling_extractor.extract, path)
            text = normalize_text(docling_result.text)

            if len(text) >= self._docling_min_text_chars:
                page = ParsedPage(
                    page_index=0,
                    text=text,
                    page_type=PageType.TEXT,
                    tables=[],
                    metadata={"backend": _BACKEND_DOCLING},
                )
                return DocumentParseResult(
                    text=text,
                    source=str(path),
                    file_type=DocumentType.PDF,
                    pages=[page],
                    tables=[],
                    metadata={
                        "parsers": self.name,
                        "selected_parser": self.name,
                        "pdf_backend": _BACKEND_DOCLING,
                        "fallback_used": False,
                        "ocr_backend": _BACKEND_PADDLEOCR,
                        "page_count": docling_result.metadata.get("page_count"),
                        "parsed_page_count": 1,
                        "docling_metadata": docling_result.metadata,
                    },
                )

            reason = "empty_text" if not text else "text_too_short"
            warnings.append(
                _format_docling_degraded_warning(
                    reason=reason,
                    detail=(
                        f"length={len(text)}, "
                        f"min_text_chars={self._docling_min_text_chars}"
                    ),
                )
            )

        except Exception as e:
            warnings.append(
                _format_docling_degraded_warning(
                    reason=type(e).__name__,
                    detail=str(e),
                )
            )

        return await self._parse_with_pymupdf(
            path,
            warnings=warnings,
        )

    async def _parse_with_pymupdf(
        self,
        path: Path,
        *,
        warnings: Optional[List[str]] = None,
    ) -> DocumentParseResult:
        """PyMuPDF 降级解析：分类页面 → 提取草稿 → OCR 扫描页 → 输出结果。"""
        warnings = list(warnings or [])

        with fitz.open(str(path)) as doc:
            total_page_count = len(doc)
            effective_page_count = min(total_page_count, self._max_pages)

            if total_page_count > self._max_pages:
                warning = (
                    f"page_truncated: PDF 共 {total_page_count} 页，"
                    f"仅解析前 {self._max_pages} 页"
                )
                warnings.append(warning)
                log_fail(
                    "PDF 页数截断",
                    warning,
                    path=str(path),
                    total_pages=total_page_count,
                    max_pages=self._max_pages,
                )

            page_type_counts: Dict[PageType, int] = {
                PageType.TEXT: 0,
                PageType.MIXED: 0,
                PageType.SCANNED: 0,
                PageType.EMPTY: 0,
            }

            with tempfile.TemporaryDirectory(prefix="wisepen-pdf-pages-") as tmp_dir:
                render_dir = Path(tmp_dir)
                drafts = await self._extract_drafts(
                    doc,
                    effective_page_count=effective_page_count,
                    page_type_counts=page_type_counts,
                    warnings=warnings,
                )
                await self._run_ocr_stages(
                    path,
                    drafts,
                    warnings=warnings,
                    render_dir=render_dir,
                )

        pages = [
            ParsedPage(
                page_index=draft.page_index,
                text=(
                    ""
                    if not draft.text
                    else f"## Page {draft.page_index + 1}\n\n{draft.text}".strip()
                ),
                page_type=draft.page_type,
                tables=draft.tables,
                metadata={
                    "page_type": draft.page_type,
                    "ocr_used": draft.ocr_used,
                    "text_backend": draft.text_backend,
                },
            )
            for draft in drafts
        ]

        tables: List[ParsedTable] = []
        for draft in drafts:
            tables.extend(draft.tables)

        text = "\n\n".join(page.text for page in pages if page.text.strip())

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=DocumentType.PDF,
            pages=pages,
            tables=tables,
            metadata={
                "parsers": self.name,
                "selected_parser": self.name,
                "pdf_backend": _BACKEND_PYMUPDF,
                "fallback_used": True,
                "ocr_backend": _BACKEND_PADDLEOCR,
                "page_type_counts": page_type_counts,
                "page_count": total_page_count,
                "parsed_page_count": effective_page_count,
            },
            warnings=warnings,
        )

    async def _extract_drafts(
        self,
        doc,
        *,
        effective_page_count: int,
        page_type_counts: Dict[PageType, int],
        warnings: List[str],
    ) -> List[PageDraft]:
        """逐页探测并生成 PageDraft，按页面类型分类处理。"""
        drafts: List[PageDraft] = []

        for page_index in range(effective_page_count):
            try:
                page = doc.load_page(page_index)
                probe = self._probe_page(
                    page,
                    page_index=page_index,
                    warnings=warnings,
                )
                page_type_counts[probe.page_type] = (
                    page_type_counts.get(probe.page_type, 0) + 1
                )

                if probe.page_type in {PageType.TEXT, PageType.MIXED}:
                    drafts.append(
                        self._draft_text_page(
                            page,
                            probe,
                            warnings=warnings,
                        )
                    )
                elif probe.page_type == PageType.SCANNED:
                    drafts.append(PageDraft(
                        page_index=probe.page_index,
                        page_type=PageType.SCANNED,
                        text="",
                        probe=probe,
                        text_backend=_BACKEND_NONE,
                    ))
                elif probe.page_type == PageType.EMPTY:
                    drafts.append(PageDraft(
                        page_index=probe.page_index,
                        page_type=PageType.EMPTY,
                        text="",
                        probe=probe,
                        text_backend=_BACKEND_NONE,
                    ))
                else:
                    raise UnsupportedDocumentFormatError(probe.page_type)

            except Exception as e:
                warning = (
                    f"page_parse_failed: page={page_index + 1}: "
                    f"{type(e).__name__}: {e}"
                )
                warnings.append(warning)
                log_fail("PDF 页面 probe", repr(e), page=page_index + 1)

        return drafts


    def _probe_page(self, page, *, page_index: int, warnings: List[str]) -> PageProbe:
        """探测单页的文本与图片信息，失败时使用 fallback 探测。"""
        try:
            return self.classifier.probe_page(page, page_index=page_index)
        except Exception as e:
            warning = (
                f"page_probe_fallback: page={page_index + 1}: "
                f"{type(e).__name__}: {e}"
            )
            warnings.append(warning)
            log_fail("PDF 页面 classifier", repr(e), page=page_index + 1)
            return self.classifier.fallback_probe_page(page, page_index=page_index)


    def _draft_text_page(
        self,
        page,
        probe: PageProbe,
        *,
        warnings: List[str],
    ) -> PageDraft:
        """从 TEXT/MIXED 页面提取原生文本和表格，构造 PageDraft。"""
        text = normalize_text(page.get_text("text", sort=True))
        if not text:
            text = probe.text

        table_text, tables = self.table_extractor.extract_native_from_page(
            page=page,
            page_index=probe.page_index,
            warnings=warnings,
        )
        if table_text:
            text = self._append_text_block(text, table_text)

        return PageDraft(
            page_index=probe.page_index,
            page_type=probe.page_type,
            text=text,
            probe=probe,
            text_backend=_BACKEND_PYMUPDF,
            tables=tables,
        )

    async def _run_ocr_stages(
        self,
        path: Path,
        drafts: List[PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
        """执行 OCR 流水线：筛选需要 OCR 的页面 → 渲染为图片 → OCR 文字识别 → 扫描页表格抽取。"""
        ocr_drafts = [draft for draft in drafts if self._should_run_ocr(draft)]
        if not ocr_drafts:
            return


        render_drafts = (
            ocr_drafts[: self._scanned_ocr_max_pages]
            if self._scanned_ocr_max_pages > 0
            else []
        )

        await self._render_ocr_pages(
            path,
            render_drafts,
            warnings=warnings,
            render_dir=render_dir,
        )
        await self._extract_page_ocr(render_drafts, warnings=warnings)
        await self._extract_scanned_table_text(
            [draft for draft in render_drafts if draft.page_type == PageType.SCANNED],
            warnings=warnings,
        )


    def _should_run_ocr(self, draft: PageDraft) -> bool:
        """判断当前页面是否需要执行 OCR：扫描页直接需要，混合页根据图片占比决策。"""
        if draft.page_type == PageType.SCANNED:
            return True

        if draft.page_type != PageType.MIXED:
            return False

        probe = draft.probe
        if probe is None or not probe.has_images:
            return False

        if probe.text_length < self.classifier._min_text_chars:
            return True

        if probe.image_area_ratio is None:
            return True

        return probe.image_area_ratio >= self.classifier._image_area_ratio


    async def _render_ocr_pages(
        self,
        path: Path,
        drafts: List[PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
        """并发渲染页面为 PNG 图片，使用信号量控制并发度。"""
        if not drafts:
            return

        semaphore = asyncio.Semaphore(self._scanned_ocr_concurrency)

        async def _render_one(draft: PageDraft) -> None:
            """使用 PyMuPDF 将单页 PDF 渲染为图片。"""
            async with semaphore:
                try:
                    draft.image_path = await asyncio.to_thread(
                        self._render_page,
                        path,
                        page_index=draft.page_index,
                        output_dir=render_dir,
                    )
                except Exception as e:
                    warning = (
                        f"page_render_failed: page={draft.page_index + 1}: "
                        f"{type(e).__name__}: {e}"
                    )
                    warnings.append(warning)
                    log_fail(
                        "PDF OCR render",
                        repr(e),
                        page=draft.page_index + 1,
                        path=str(path),
                    )

        await asyncio.gather(*(_render_one(draft) for draft in drafts))


    def _render_page(
        self,
        path: Path,
        *,
        page_index: int,
        output_dir: Path,
    ) -> Path:
        """将 PDF 指定页面渲染为 PNG 图片，校验像素上限防止 OOM。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            scale = self._render_dpi / 72.0
            estimated_width = max(1, math.ceil(page.rect.width * scale))
            estimated_height = max(1, math.ceil(page.rect.height * scale))
            estimated_pixels = estimated_width * estimated_height

            if estimated_pixels > self._render_max_image_pixels:
                raise DocumentParseError(
                    f"PDF page render exceeds pixel limit: "
                    f"{estimated_pixels} > {self._render_max_image_pixels}"
                )

            pix = page.get_pixmap(dpi=self._render_dpi, alpha=self._render_alpha)
            actual_pixels = pix.width * pix.height

            if actual_pixels > self._render_max_image_pixels:
                raise DocumentParseError(
                    f"PDF page render exceeds pixel limit: "
                    f"{actual_pixels} > {self._render_max_image_pixels}"
                )

            image_path = output_dir / f"page_{page_index + 1}.png"
            pix.save(str(image_path))
            return image_path


    async def _extract_page_ocr(
        self,
        drafts: List[PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        """并发对渲染后的页面执行 OCR 文字识别，将识别文本追加到 PageDraft。"""
        eligible = [draft for draft in drafts if draft.image_path is not None]
        if not eligible:
            return

        semaphore = asyncio.Semaphore(self._scanned_ocr_concurrency)

        async def _ocr_one(draft: PageDraft) -> None:
            """对单页图片执行 OCR 文字识别。"""
            if draft.image_path is None:
                return

            async with semaphore:
                try:
                    ocr_text = await self.ocr_adapter.extract_text(draft.image_path)
                    draft.text = self._append_text_block(draft.text, ocr_text)
                    draft.ocr_used = True
                    draft.text_backend = self._merge_text_backend(
                        draft.text_backend,
                        _BACKEND_PADDLEOCR,
                    )
                except Exception as e:
                    warning = (
                        f"ocr_failed: page={draft.page_index + 1}: "
                        f"{type(e).__name__}: {e}"
                    )
                    warnings.append(warning)
                    log_fail("PDF OCR", repr(e), page=draft.page_index + 1)

        await asyncio.gather(*(_ocr_one(draft) for draft in eligible))


    async def _extract_scanned_table_text(
        self,
        drafts: List[PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        """对扫描页渲染图片执行表格结构识别，将表格文本追加到 PageDraft。"""
        eligible = [draft for draft in drafts if draft.image_path is not None]
        if not eligible:
            return

        async def _extract_one(draft: PageDraft) -> None:
            """对扫描页渲染图片执行表格结构识别。"""
            if draft.image_path is None:
                return

            try:
                table_text, tables = await asyncio.to_thread(
                    self.table_extractor.extract_scanned_from_image,
                    image_path=draft.image_path,
                    page_index=draft.page_index,
                )
                if table_text:
                    draft.text = self._append_text_block(draft.text, table_text)
                    draft.tables.extend(tables)
            except Exception as e:
                warning = (
                    f"scanned_table_text_failed: page={draft.page_index + 1}: "
                    f"{type(e).__name__}: {e}"
                )
                warnings.append(warning)
                log_fail(
                    "PDF scanned table text",
                    repr(e),
                    page=draft.page_index + 1,
                )

        await asyncio.gather(*(_extract_one(draft) for draft in eligible))


    def _append_text_block(self, text: str, block: str) -> str:
        """将两个文本块用双换行拼接，跳过空块。"""
        parts = [part for part in [text, block] if part]
        return "\n\n".join(parts)


    def _merge_text_backend(self, current: str, addition: str) -> str:
        """合并文本来源后端标记，使用 '+' 分隔多来源。"""
        if not current or current == _BACKEND_NONE:
            return addition

        if current == addition:
            return current

        backends = current.split("+")
        if addition not in backends:
            backends.append(addition)

        return "+".join(backends)


def _format_docling_degraded_warning(*, reason: str, detail: str) -> str:
    payload = {
        "code": "docling_failed",
        "reason": reason,
        "detail": detail,
        "fallback": "pymupdf_paddleocr",
        "severity": "degraded_not_failed",
        "user_impact": (
            "PDF parsing continued with PyMuPDF/OCR fallback; main text remains usable, "
            "but Docling table-structure extraction may be incomplete."
        ),
    }
    return "parse_warning: " + json.dumps(payload, ensure_ascii=False)
