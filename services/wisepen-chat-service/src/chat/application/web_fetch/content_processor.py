from io import BytesIO
from typing import Callable, Dict, List, Optional
from zipfile import BadZipFile, ZipFile

import pdfplumber
from docx import Document as DocxDocument
from markdownify import markdownify
from openpyxl import load_workbook
from pptx import Presentation
from readability import Document

from common.logger import log_ok, log_fail


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE 复合文档文件头，用于识别旧版 .doc/.xls/.ppt

_ANTI_CRAWL_KEYWORDS = (  # 纯文本响应中的反爬/错误页面特征词，命中则触发降级
    "just a moment",
    "please enable javascript",
    "please enable js",
    "captcha",
    "access denied",
    "cloudflare",
    "are you a robot",
    "are you human",
    "verify you are human",
)


class ContentProcessor:
    def __init__(
        self,
        min_content_length: int = 400,
        max_document_size: int = 50 * 1024 * 1024,
    ):
        self._min_content_length = min_content_length
        self._max_document_size = max_document_size
        self._doc_parsers: Dict[str, Callable] = {
            "pdf": self._parse_pdf,
            "docx": self._parse_docx,
            "xlsx": self._parse_xlsx,
            "pptx": self._parse_pptx,
        }

    def process(self, content: str | bytes) -> Optional[str]:
        if isinstance(content, bytes):
            return self._process_document(content)
        return self._process_html(content)

    def _process_html(self, html: str) -> Optional[str]:
        stripped = html.strip()
        lower = stripped.lower()

        if "<html" in lower[:1024]:
            try:
                clean_content = self._extract_main_content(stripped)
                result = self._convert_to_markdown(clean_content)

                if len(result.strip()) < self._min_content_length:
                    return None

                return result
            except Exception as e:
                log_fail("HTML 清洗", e, fallback="返回未清洗原文，可能含 HTML 标签")
                return stripped

        if len(stripped) < self._min_content_length:
            return None
        if any(kw in lower for kw in _ANTI_CRAWL_KEYWORDS):
            log_fail("纯文本检测", "疑似反爬/错误页面，触发降级")
            return None
        return stripped

    def _process_document(self, data: bytes) -> Optional[str]:
        if len(data) > self._max_document_size:
            log_fail("文档解析", f"文件过大({len(data)}字节)，上限{self._max_document_size}字节")
            return None

        doc_type = self._detect_doc_type(data)
        if doc_type is None:
            return None

        parser = self._doc_parsers.get(doc_type)
        if parser is None:
            log_fail("文档解析", f"暂不支持的文档类型: {doc_type}")
            return None

        text = parser(data)
        if text is None:
            log_fail("文档清洗", "文本提取返回空")
            return None
        if len(text.strip()) < self._min_content_length:
            log_fail("文档清洗", f"提取文本过短({len(text.strip())}字符)，触发降级")
            return None
        log_ok("文档清洗", length=len(text.strip()))
        return text

    def _detect_doc_type(self, data: bytes) -> Optional[str]:
        if data[:5] == b"%PDF-":
            return "pdf"

        if data[:8] == _OLE_MAGIC:
            log_fail("文档解析", "OLE 复合文档(旧版 .doc/.xls/.ppt)，暂不支持")
            return None

        try:
            with ZipFile(BytesIO(data)) as zf:
                names = zf.namelist()
                matches = [
                    ("word/document.xml", "docx"),
                    ("xl/workbook.xml", "xlsx"),
                    ("ppt/presentation.xml", "pptx"),
                ]
                hits = [doc_type for sig, doc_type in matches if sig in names]
                if len(hits) != 1:
                    if hits:
                        log_fail("文档解析", f"ZIP 内含多种 Office 特征文件({', '.join(hits)})，无法确定类型")
                    return None
                return hits[0]
        except BadZipFile:
            pass

        log_fail("文档解析", "无法识别文档类型")
        return None

    def _extract_main_content(self, html: str) -> str:
        try:
            return Document(html).summary()
        except Exception as e:
            log_fail("readability 提取主体内容", e)
            return html

    def _convert_to_markdown(self, html: str) -> str:
        try:
            return markdownify(
                html,
                heading_style="ATX",
                strip=["script", "style", "img", "nav", "footer"],
                autolinks=True,
            ).strip()
        except Exception as e:
            log_fail("HTML 转 Markdown", e)
            return html

    def _parse_pdf(self, data: bytes) -> Optional[str]:
        try:
            text_parts: List[str] = []
            page_count = 0
            with pdfplumber.open(BytesIO(data)) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            result = "\n".join(text_parts).strip()
            if result:
                log_ok("PDF 文本提取", pages=page_count, length=len(result))
                return result
            return None
        except Exception as e:
            log_fail("PDF 文本提取", e)
            return None

    def _parse_docx(self, data: bytes) -> Optional[str]:
        try:
            doc = DocxDocument(BytesIO(data))
            parts: List[str] = []
            for para in doc.paragraphs:
                if para.text.strip():
                    parts.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    parts.append("\t".join(cells))
            result = "\n".join(parts).strip()
            if result:
                log_ok("DOCX 文本提取", length=len(result))
                return result
            return None
        except Exception as e:
            log_fail("DOCX 文本提取", e)
            return None

    def _parse_xlsx(self, data: bytes) -> Optional[str]:
        wb = None
        try:
            wb = load_workbook(BytesIO(data), read_only=True)
            parts: List[str] = []
            for sheet in wb:
                parts.append(f"Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(cell) if cell is not None else "" for cell in row]
                    line = "\t".join(cells)
                    if line.strip():
                        parts.append(line)
            result = "\n".join(parts).strip()
            if result:
                log_ok("XLSX 文本提取", length=len(result))
                return result
            return None
        except Exception as e:
            log_fail("XLSX 文本提取", e)
            return None
        finally:
            if wb is not None:
                wb.close()

    def _parse_pptx(self, data: bytes) -> Optional[str]:
        try:
            prs = Presentation(BytesIO(data))
            parts: List[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        text = shape.text.strip()
                        if text:
                            parts.append(text)
            result = "\n".join(parts).strip()
            if result:
                log_ok("PPTX 文本提取", length=len(result))
                return result
            return None
        except Exception as e:
            log_fail("PPTX 文本提取", e)
            return None
