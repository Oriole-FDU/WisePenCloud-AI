from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from chat.application.tools.document.services.document_parse.utils.text import normalize_text


@dataclass(slots=True)
class DoclingPdfResult:
    """Docling PDF 解析结果。"""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class DoclingPdfExtractor:
    """Docling PDF 解析器，默认启用表格结构识别并关闭 OCR。"""

    def __init__(
        self,
        converter: Optional[DocumentConverter] = None,
        *,
        do_table_structure: bool = True,
        do_ocr: bool = False,
    ) -> None:
        self._do_table_structure = do_table_structure
        self._do_ocr = do_ocr
        self.converter = converter or self._build_converter(
            do_table_structure=do_table_structure,
            do_ocr=do_ocr,
        )

    def extract(self, path: Path) -> DoclingPdfResult:
        """使用 Docling 将 PDF 转换为 Markdown 文本。"""
        result = self.converter.convert(str(path))
        text = normalize_text(result.document.export_to_markdown())

        metadata: Dict[str, Any] = {
            "do_ocr": self._do_ocr,
            "do_table_structure": self._do_table_structure,
        }

        page_count = self._get_page_count(result.document)
        if page_count is not None:
            metadata["page_count"] = page_count

        return DoclingPdfResult(
            text=text,
            metadata=metadata,
        )

    def _build_converter(
        self,
        *,
        do_table_structure: bool,
        do_ocr: bool,
    ) -> DocumentConverter:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = do_ocr
        pipeline_options.do_table_structure = do_table_structure

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options,
                ),
            }
        )

    def _get_page_count(self, document: Any) -> Optional[int]:
        pages = getattr(document, "pages", None)
        if pages is None:
            return None
        return len(pages)
