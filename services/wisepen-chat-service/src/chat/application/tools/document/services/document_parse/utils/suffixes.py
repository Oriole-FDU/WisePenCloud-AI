from pathlib import Path

from chat.application.tools.document.services.document_parse.enums import DocumentType
from chat.application.tools.document.services.document_parse.errors import (
    UnsupportedDocumentFormatError,
)

# 各大类文档支持的后缀名集合
PDF_SUFFIXES = {".pdf"}
OFFICE_SUFFIXES = {".docx", ".docm", ".pptx", ".pptm"}
EPUB_SUFFIXES = {".epub"}
SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".ods"}

# 后缀名到内部 DocumentType 枚举的映射表
_DOCUMENT_TYPE_BY_SUFFIX = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".docm": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".pptm": DocumentType.PPTX,
    ".epub": DocumentType.EPUB,
    ".xlsx": DocumentType.SPREADSHEET,
    ".xls": DocumentType.SPREADSHEET,
    ".xlsm": DocumentType.SPREADSHEET,
    ".ods": DocumentType.SPREADSHEET,
}

# 针对不支持的后缀名，提供具体的重定向引导建议
_UNSUPPORTED_GUIDANCE = {
    ".html": ".html pages should be handled by web_fetch.",
    ".htm": ".htm pages should be handled by web_fetch.",
    ".txt": ".txt should be read directly by the caller, not document_parse.",
    ".md": ".md should be read directly by the caller, not document_parse.",
    ".markdown": ".markdown should be read directly by the caller, not document_parse.",
    ".csv": ".csv should be read directly by the caller, not document_parse.",
    ".json": ".json should be read directly by the caller, not document_parse.",
    ".xml": ".xml should be read directly by the caller, not document_parse.",
    ".png": ".png images should be handled by the multimodal model, not document_parse.",
    ".jpg": ".jpg images should be handled by the multimodal model, not document_parse.",
    ".jpeg": ".jpeg images should be handled by the multimodal model, not document_parse.",
    ".webp": ".webp images should be handled by the multimodal model, not document_parse.",
    ".tiff": ".tiff images should be handled by the multimodal model, not document_parse.",
    ".tif": ".tif images should be handled by the multimodal model, not document_parse.",
    ".bmp": ".bmp images should be handled by the multimodal model, not document_parse.",
    ".gif": ".gif images should be handled by the multimodal model, not document_parse.",
    ".mp3": ".mp3 audio should be handled by the multimodal model, not document_parse.",
    ".wav": ".wav audio should be handled by the multimodal model, not document_parse.",
    ".mp4": ".mp4 video should be handled by the multimodal model, not document_parse.",
    ".mov": ".mov video should be handled by the multimodal model, not document_parse.",
}


def detect_document_type_by_suffix(path: Path) -> DocumentType:
    """根据文件路径的后缀名检测并返回对应的文档类型。

    如果后缀缺失或属于不支持的格式，将抛出 UnsupportedDocumentFormatError。
    """
    suffix = path.suffix.lower()

    # 校验后缀是否存在
    if not suffix:
        raise UnsupportedDocumentFormatError("missing file suffix")

    # 匹配支持的文档类型
    document_type = _DOCUMENT_TYPE_BY_SUFFIX.get(suffix)
    if document_type:
        return document_type

    # 未匹配到时，获取对应的引导建议并抛出异常
    guidance = _UNSUPPORTED_GUIDANCE.get(suffix, "")
    raise UnsupportedDocumentFormatError(suffix, guidance)