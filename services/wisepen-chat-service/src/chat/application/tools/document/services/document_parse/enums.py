from enum import StrEnum


class DocumentType(StrEnum):
    """文档类型枚举，用于解析器路由与结果标注。"""
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    EPUB = "epub"
    SPREADSHEET = "spreadsheet"


class PageType(StrEnum):
    """PDF 页面类型枚举，用于分类处理策略选择。"""
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    TEXT = "text"
    MIXED = "mixed"
    SCANNED = "scanned"
    EMPTY = "empty"


class ParserName(StrEnum):
    """解析器/后端名称枚举，用于元数据标记与 fallback 链路追踪。"""
    PDF = "PdfParser"
    OFFICE = "OfficeParser"
    SPREADSHEET = "SpreadsheetParser"
    DOCLING = "docling"
    DOCLING_PDF_TABLE_NO_OCR = "docling_pdf_table_no_ocr"
    MARKITDOWN = "markitdown"
    PANDAS = "pandas"
    PYMUPDF = "pymupdf"
    PADDLEOCR = "paddleocr"
    PP_STRUCTURE = "pp_structure"
